"""Bounded, explicit S10 source snapshots; never a production recrawl/index job."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
from pathlib import Path
import sqlite3
import urllib.parse
import urllib.request

from .chunk_batches import read_json, read_rows
from .io import canonical_json, file_hash, text_hash, write_json
from .retrieval_contracts import distinct, require, signed


class VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style'}: self.hidden += 1
        elif tag in {'p', 'div', 'br', 'h4'}: self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in {'script', 'style'}: self.hidden = max(0, self.hidden - 1)
        elif tag in {'p', 'div', 'h4'}: self.parts.append('\n')

    def handle_data(self, data):
        if not self.hidden: self.parts.append(data)


def visible(value):
    parser = VisibleText(); parser.feed(value)
    return '\n'.join(' '.join(line.split()) for line in ''.join(parser.parts).splitlines() if line.strip())


def download(url, path):
    """Archive bytes and HTTP provenance; an HTTP-200 gate is not evidence."""
    path = Path(path)
    if path.exists():
        receipt = read_json(path.with_suffix('.receipt.json'))
        require(receipt['url'] == url and receipt['sha256'] == file_hash(path), 'Downloaded source changed')
        return path.read_bytes(), receipt
    request = urllib.request.Request(url, headers={'User-Agent': 'ViBioMIR-S10-research/1.0'})
    with urllib.request.urlopen(request, timeout=40) as response:
        data = response.read(10_000_001)
        require(len(data) <= 10_000_000, 'Source exceeds 10 MB bounded download')
        receipt = {'url': url, 'final_url': response.url, 'http_status': response.status,
                   'content_type': response.headers.get('Content-Type'),
                   'retrieved_at_utc': datetime.now(timezone.utc).isoformat()}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    receipt['sha256'] = file_hash(path)
    write_json(path.with_suffix('.receipt.json'), receipt)
    return data, receipt


def article_packet(record, doc_id, url):
    require(record.get('id') and record.get('abstractText'), 'PubMed record has no abstract')
    text = visible(record['abstractText'])
    require(len(text) >= 100 and record.get('title'), 'Empty/short article metadata')
    version = text_hash(canonical_json([url, text]))
    region = {'region_id': text_hash('abstract:' + version), 'text': text, 'source_start': 0,
              'source_end': len(text), 'source_kind': 'europepmc_pubmed_abstract',
              'source_locator_json': canonical_json({'pubmed_id': record['id'], 'field': 'abstractText'}),
              'units': [{'start': 0, 'end': len(text), 'kind': 'p', 'is_heading': False, 'heading': []}]}
    return {'doc_id': doc_id, 'derived_version_id': version, 'parent_version_id': None,
            'availability': 'content', 'title': visible(record['title']), 'language_hint': 'en',
            'review_language': 'en', 'url': url, 'regions': [region], 'unmapped_blocks': 0,
            'source_scope': 'S10-only official-URL supplement; absent from production S05/S08'}


def collect(config_path='configs/stage10_silver_sources.json'):
    import pyarrow.parquet as pq
    c = read_json(config_path); w = read_json(c['workflow']); root = Path(c['root'])
    distinct(root, w['input_dir'], w['stage3_dir'], w['stage4_dir'])
    root.mkdir(parents=True, exist_ok=True)
    require(not (root / 'CORPUS.json').exists(), 'Corpus already frozen; reuse it or choose a new root')
    with sqlite3.connect(Path(w['qa_dir'], 'catalog.sqlite3').resolve().as_uri()+'?mode=ro&immutable=1', uri=True) as db:
        ids = set(c['local_positive_ids'])
        for language in ('vi_diacritics', 'cjk_likely_zh'):
            rows = db.execute('SELECT doc_id FROM documents WHERE language=? AND characters BETWEEN 1000 AND 3000 ORDER BY priority', (language,))
            added = 0
            for (doc_id,) in rows:
                if doc_id not in ids:
                    ids.add(doc_id); added += 1
                    if added == c['local_distractors_per_language']: break
        locations = {i: db.execute('SELECT batch,url,language FROM documents WHERE doc_id=?', (i,)).fetchone() for i in ids}
    require(all(locations.values()), 'Some requested documents are absent from the local catalog')
    parent = read_json(Path(w['input_dir']) / 'manifest.json')
    packets = {}
    for batch in sorted({row[0] for row in locations.values()}):
        path = Path(w['input_dir']) / f'batch-{batch:06d}'; info = read_json(path / 'batch.json')
        require(info['signature'] == parent['signature'] and file_hash(path / 'documents.jsonl.gz') == info['sha256'], 'Local source batch changed')
        for row in read_rows(path):
            if row['doc_id'] in ids:
                loc = locations[row['doc_id']]
                language = c.get('positive_language_by_id', {}).get(str(row['doc_id']))
                language = language or ('zh' if loc[2] == 'cjk_likely_zh' else 'vi' if loc[2] == 'vi_diacritics' else 'unknown')
                row.update(url=loc[1], review_language=language,
                           source_scope='existing S05 source')
                packets[row['doc_id']] = row
    require(set(packets) == ids, 'Local document extraction incomplete')
    links = pq.read_table(c['official_links'], columns=['id','url']).to_pylist()
    by_pmid = {int(row['url'].rstrip('/').split('/')[-1]): row for row in links if 'pubmed.ncbi.nlm.nih.gov/' in row['url']}
    wanted = [i for i in c['pubmed_ids'] if i in by_pmid]
    require(len(wanted) >= 20, 'Choose 20 PubMed IDs present in the official allowlist')
    query = 'SRC:MED AND (' + ' OR '.join('EXT_ID:'+str(i) for i in wanted) + ')'
    url = 'https://www.ebi.ac.uk/europepmc/webservices/rest/search?' + urllib.parse.urlencode({'query':query,'format':'json','resultType':'core','pageSize':100})
    data, receipt = download(url, root / 'research' / 'europepmc_abstracts.json')
    records = {int(r['id']):r for r in json.loads(data)['resultList']['result'] if r.get('abstractText')}
    require(set(wanted) <= set(records), 'Missing requested abstracts; replace missing IDs explicitly, do not invent evidence')
    for pmid in wanted:
        row = by_pmid[pmid]; packets[row['id']] = article_packet(records[pmid], row['id'], row['url'])
    # Fetch the twenty positive local pages for online/source comparison only.
    def check(doc_id):
        packet = packets[doc_id]
        try:
            raw, meta = download(packet['url'], root / 'research' / f'page-{doc_id}.html')
            text = visible(raw.decode('utf-8', errors='replace'))
            probes = [r['text'].splitlines()[0] for r in packet['regions'] if r['text'].strip()]
            meta = {**meta, 'local_title_or_region_heading_found': any(p in text for p in probes),
                    'evidence_authority': 'immutable local S05 text; live pages can differ'}
        except Exception as exc:
            meta = {'url':packet['url'], 'online_fetch':'unavailable', 'error':str(exc),
                    'evidence_authority':'local snapshot only; no online verification claimed'}
        return str(doc_id), meta
    with ThreadPoolExecutor(max_workers=3) as pool: online = dict(pool.map(check, c['local_positive_ids']))
    value = signed({'schema':'vibiomir-s10-corpus-v1','scope':'bounded S10 corpus; not the production index',
                    'source_input_signature':parent['signature'],'official_links_sha256':file_hash(c['official_links']),
                    'configuration':c,'pubmed_receipt':receipt,'online_checks':online,
                    'documents':[packets[i] for i in sorted(packets)]})
    write_json(root / 'CORPUS.json', value)
    return {'state':'complete','documents':len(packets),'online_checks':len(online),'root':str(root)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--config',default='configs/stage10_silver_sources.json')
    args = parser.parse_args()
    try: print(json.dumps(collect(args.config),indent=2))
    except (ValueError,OSError,KeyError,TypeError) as exc: parser.exit(2,str(exc)+'\n')
