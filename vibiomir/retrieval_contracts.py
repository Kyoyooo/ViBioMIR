"""Portable S07/S08 source identities, stable global row IDs and candidate outputs."""
import gzip
import json
from pathlib import Path

from .chunk_batches import batches, read_json, read_rows
from .io import canonical_json, file_hash, text_hash, write_json


def require(ok, message):
    if not ok:
        raise ValueError(message)


def distinct(output, *sources):
    out = Path(output).resolve()
    for source in sources:
        path = Path(source).resolve()
        require(out != path and out not in path.parents and path not in out.parents, 'Output overlaps a source/other output')


def signed(value):
    return {**value, 'signature': text_hash(canonical_json(value))}


def checked(path):
    value = read_json(path)
    require(value.get('signature') == text_hash(canonical_json({k: v for k, v in value.items() if k != 'signature'})),
            f'Identity checksum differs: {path}')
    return value


def chunk_snapshot(root):
    root = Path(root)
    require((root / 'manifest.json').is_file() and (root / 'VALIDATION.json').is_file(), 'Use a completed, validated stage-5 chunk store')
    manifest = read_json(root / 'manifest.json')
    require(manifest['signature'] == text_hash(canonical_json(manifest['identity'])), 'Chunk manifest identity differs')
    validation = read_json(root / 'VALIDATION.json')
    require(manifest.get('state') == 'complete' and manifest['identity']['operation'] == 'build-chunks' and
            validation.get('validation') == 'passed' and all(manifest[k] == validation[k] for k in ('documents', 'chunks')),
            'Completed stage-5 build with passed validation required')
    return {'signature': manifest['signature'], 'manifest_sha256': file_hash(root / 'manifest.json'),
            'validation_sha256': file_hash(root / 'VALIDATION.json'),
            **{k: manifest[k] for k in ('documents', 'chunks', 'batches')}}


def source_parts(root, snapshot):
    offset, documents, parts = 0, 0, []
    for path, info in batches(root, snapshot['signature']):
        parts.append({'index': info['index'], 'rows': info['chunks'], 'documents': info['documents'],
                      'start': offset, 'source_sha256': info['sha256'], 'batch_json_sha256': file_hash(path / 'batch.json')})
        offset += info['chunks']
        documents += info['documents']
    require(offset == snapshot['chunks'] and documents == snapshot['documents'] and len(parts) == snapshot['batches'], 'Source totals differ')
    return parts


def chunk_rows(root, part):
    path = Path(root) / f"batch-{part['index']:06d}"
    require(file_hash(path / 'batch.json') == part['batch_json_sha256'] and
            file_hash(path / 'documents.jsonl.gz') == part['source_sha256'], 'Source batch changed')
    count, documents = 0, 0
    for row_index, document in enumerate(read_rows(path)):
        documents += 1
        for chunk_order, chunk in enumerate(document['chunks']):
            require(chunk['doc_id'] == document['doc_id'] and chunk['derived_version_id'] == document['derived_version_id'], 'Chunk source relationship differs')
            require(isinstance(chunk['retrieval_text'], str) and chunk['retrieval_text'].strip(), 'Empty retrieval_text')
            require(text_hash(chunk['chunk_text']) == chunk['text_sha256'], 'Source evidence checksum differs')
            require(type(chunk['doc_id']) is int and isinstance(chunk['chunk_id'], str), 'Invalid chunk/document ID')
            yield {'row_id': part['start'] + count, 'chunk_id': chunk['chunk_id'], 'doc_id': chunk['doc_id'],
                   'derived_version_id': chunk['derived_version_id'], 'source_batch': part['index'],
                   'source_row': row_index, 'chunk_order': chunk_order, 'language_hint': chunk.get('language_hint'),
                   'retrieval_text_sha256': text_hash(chunk['retrieval_text']), 'retrieval_text': chunk['retrieval_text']}
            count += 1
    require(count == part['rows'] and documents == part['documents'], 'Source row/document accounting differs')


def mapping(row):
    return {k: v for k, v in row.items() if k != 'retrieval_text'}


def queries(path):
    path = Path(path)
    if path.suffix == '.parquet':
        import pyarrow.parquet as pq
        rows = pq.read_table(path, columns=['id', 'query']).to_pylist()
    else:
        value = read_json(path)
        rows = value['queries'] if isinstance(value, dict) else value
    require(isinstance(rows, list) and rows, 'Nonempty query list required')
    seen, result = set(), []
    for row in rows:
        require(type(row['id']) is int and row['id'] not in seen and isinstance(row['query'], str) and row['query'].strip(),
                'Query IDs must be unique integers with nonempty text')
        seen.add(row['id'])
        result.append({'id': row['id'], 'query': row['query']})
    return result


def write_jsonl_gz(path, rows):
    with gzip.open(path, 'wt', encoding='utf-8', compresslevel=3) as stream:
        for row in rows:
            stream.write(canonical_json(row) + '\n')


def read_jsonl_gz(path):
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        for line in stream:
            yield json.loads(line)


def open_run(output, identity):
    output = Path(output)
    if (output / 'manifest.json').exists():
        manifest = read_json(output / 'manifest.json')
        require(manifest['signature'] == text_hash(canonical_json(identity)) and manifest['identity'] == identity,
                'Run input/code/configuration changed; use a new output directory')
        return manifest
    require(not output.exists() or not any(output.iterdir()), 'Nonempty output has no manifest; inspect it and use a new directory')
    output.mkdir(parents=True, exist_ok=True)
    manifest = {'identity': identity, 'signature': text_hash(canonical_json(identity)), 'state': 'building',
                'human_qa': 'pending', 'selection_is_final': False, 'ready_for_retrieval': False}
    write_json(output / 'manifest.json', manifest)
    return manifest
