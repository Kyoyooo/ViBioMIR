"""Standalone, resumable Qwen scoring of submission evidence and optional subspans.

Source verification here means exact slices of the input submission, not the corpus.
No dependency on frozen BGE producer code. No official relevance evaluator included.
"""
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import zipfile

MODEL_ID = 'Qwen/Qwen3-Reranker-0.6B'
REVISION = '6e9e69830b95c52b5fd889b7690dda3329508de3'
MODEL_DATE = '2025-06-09T05:59:29Z'
EXPECTED_QUERY_SHA256 = 'ce13508b893bbd6b9169679d540630bc37eb5950cad08b4d5e0e5d7063670c08'
EXPECTED_ZIP_SHA256 = '0963641cb422e4223d6fc4b0c22eb09938f8b03c8227053cc7a398bf33e25dbd'
PREFIX = ('<|im_start|>system\nJudge whether the Document meets the requirements based on the Query '
          'and the Instruct provided. Note that the answer can only be "yes" or "no".'
          '<|im_end|>\n<|im_start|>user\n')
SUFFIX = '<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n'
INSTRUCTION = 'Given a web search query, retrieve relevant passages that answer the query'


def require(ok, message):
    if not ok:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024**2), b''):
            h.update(block)
    return h.hexdigest()


def signature(value):
    return digest(canonical(value).encode())


def signed(value):
    return {**value, 'signature': signature(value)}


def checked(value):
    require(isinstance(value, dict) and value.get('signature') ==
            signature({k: v for k, v in value.items() if k != 'signature'}), 'Record checksum differs')
    return value


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(canonical(value), encoding='utf-8')
    temp.replace(path)


def generation_policy(include_spans=False):
    return dict(version='sentence-windows-v1', include_spans=include_spans,
                min_parent_chars=350, min_span_chars=100, sentence_windows=[1, 2, 3],
                max_subspans_per_parent=4)


def model_policy():
    return dict(model_id=MODEL_ID, revision=REVISION, commit_date=MODEL_DATE,
                instruction=INSTRUCTION, max_pair_tokens=4096, truncation='reject',
                scoring='logit_yes_minus_logit_no', dtype='float16',
                attention='sdpa', template_version='official-transformers-prefix-suffix-v1')


def sentence_spans(text):
    """Conservative boundaries; preserve offsets and avoid splitting decimals/abbreviations on '.'."""
    boundaries = [0]
    for match in re.finditer(r'\n+|[!?。！？]+[ \t]*', text):
        boundaries.append(match.end())
    # Split full stops only before a likely next sentence. Decimals like 0.10 stay intact.
    for match in re.finditer(r'\.(?:[ \t]+)(?=[A-ZÀ-Ỹ])', text):
        boundaries.append(match.end())
    boundaries = sorted(set(boundaries + [len(text)]))
    result = []
    for a, b in zip(boundaries, boundaries[1:]):
        while a < b and text[a].isspace():
            a += 1
        while b > a and text[b-1].isspace():
            b -= 1
        if a < b:
            result.append((a, b))
    return result


def evenly_choose(values, count):
    if len(values) <= count:
        return values
    if count == 1:
        return [values[len(values)//2]]
    return [values[round(i * (len(values)-1)/(count-1))] for i in range(count)]


def validate_generation(policy):
    require(policy['version'] == 'sentence-windows-v1' and type(policy['include_spans']) is bool,
            'Unsupported generation policy')
    require(all(type(policy[k]) is int and policy[k] > 0 for k in
                ('min_parent_chars', 'min_span_chars', 'max_subspans_per_parent')), 'Positive span budgets required')
    require(policy['sentence_windows'] and all(type(n) is int and n > 0 for n in policy['sentence_windows']),
            'Positive sentence-window sizes required')


def make_candidates(row, policy):
    validate_generation(policy)
    candidates = []
    for index, parent in enumerate(row['relevant_chunks']):
        text = parent['chunk_text']
        spans = [(0, len(text), 'original')]
        if policy['include_spans'] and len(text) >= policy['min_parent_chars']:
            sentences = sentence_spans(text)
            extras = set()
            for width in policy['sentence_windows']:
                for start in range(len(sentences)-width+1):
                    a, b = sentences[start][0], sentences[start+width-1][1]
                    if b-a >= policy['min_span_chars'] and b-a < len(text):
                        extras.add((a, b))
            spans.extend((a, b, 'subspan') for a, b in evenly_choose(
                sorted(extras), policy['max_subspans_per_parent']))
        parent_hash = digest(text.encode())
        for a, b, kind in spans:
            value = dict(parent_index=index, parent_text_sha256=parent_hash,
                         doc_id=parent['doc_id'], start=a, end=b, kind=kind,
                         text_sha256=digest(text[a:b].encode()))
            candidates.append({**value, 'candidate_id': signature(value)})
    return candidates


def evidence_text(row, candidate):
    require(type(candidate['parent_index']) is int and 0 <= candidate['parent_index'] < len(row['relevant_chunks']),
            'Unknown parent index')
    parent = row['relevant_chunks'][candidate['parent_index']]
    text = parent['chunk_text']
    a, b = candidate['start'], candidate['end']
    require(type(a) is int and type(b) is int and 0 <= a < b <= len(text), 'Invalid evidence offsets')
    require(parent['doc_id'] == candidate['doc_id'] and digest(text.encode()) == candidate['parent_text_sha256']
            and digest(text[a:b].encode()) == candidate['text_sha256'], 'Evidence differs from parent submission')
    return text[a:b]


def load_inputs(submission_zip, query_file, expected_zip=EXPECTED_ZIP_SHA256,
                expected_query=EXPECTED_QUERY_SHA256, expected_queries=1200):
    require(file_hash(submission_zip) == expected_zip, 'Wrong input ZIP; select the original submission_17.zip')
    require(file_hash(query_file) == expected_query, 'Official query file differs')
    with zipfile.ZipFile(submission_zip) as z:
        require(z.namelist() == ['submission.json'], 'Expected exactly one root submission.json')
        raw = z.read('submission.json')
    rows = json.loads(raw)
    import pyarrow.parquet as pq
    queries = pq.read_table(query_file, columns=['id', 'query']).to_pylist()
    validate_input_rows(rows, queries, expected_queries)
    return rows, queries, dict(submission_zip_sha256=expected_zip, submission_json_sha256=digest(raw),
                               query_sha256=expected_query), raw


def validate_input_rows(rows, queries, expected_queries):
    require(isinstance(rows, list) and len(rows) == len(queries) == expected_queries, 'Query count differs')
    ids = [q['id'] for q in queries]
    require(all(type(i) is int for i in ids) and len(set(ids)) == len(ids) and [r['id'] for r in rows] == ids,
            'Query IDs/order differ or duplicate IDs')
    for row, query in zip(rows, queries):
        require(isinstance(query['query'], str) and query['query'].strip(), 'Empty query')
        docs = row['relevant_docs']
        require(all(type(d) is int for d in docs) and len(set(docs)) == len(docs), 'Invalid doc list')
        for i, c in enumerate(row['relevant_chunks']):
            require(type(c['doc_id']) is int and c['doc_id'] in docs and isinstance(c['chunk_text'], str)
                    and c['chunk_text'].strip() and type(c['chunk_order']) is int and c['chunk_order'] == i,
                    'Invalid parent evidence')


def run_identity(inputs, generation, policy):
    validate_generation(generation)
    require(policy['model_id'] == MODEL_ID and policy['revision'] == REVISION and policy['commit_date'] == MODEL_DATE,
            'Use the pinned Qwen checkpoint')
    return dict(operation='qwen-submission-rescore-v1', inputs=inputs, generation=generation,
                model_policy=policy, code_sha256=file_hash(__file__), source_scope='input_submission_only')


def formatted_input(query, passage, policy):
    body = f"<Instruct>: {policy['instruction']}\n<Query>: {query}\n<Document>: {passage}"
    return PREFIX + body + SUFFIX


def encoded_pair(tokenizer, query, passage, policy):
    # Exactly the official prefix/body/suffix assembly, with no silent truncation.
    body = f"<Instruct>: {policy['instruction']}\n<Query>: {query}\n<Document>: {passage}"
    ids = (tokenizer.encode(PREFIX, add_special_tokens=False)
           + tokenizer.encode(body, add_special_tokens=False)
           + tokenizer.encode(SUFFIX, add_special_tokens=False))
    require(len(ids) <= policy['max_pair_tokens'],
            f"Pair has {len(ids)} tokens > {policy['max_pair_tokens']}; increase policy budget in a new run")
    return dict(input_ids=ids, attention_mask=[1]*len(ids))


class QwenScorer:
    def __init__(self, policy, batch_size=8, padded_tokens=8192):
        import torch
        import transformers
        from transformers import AutoModelForCausalLM, AutoTokenizer
        require(torch.cuda.is_available(), 'Select a CUDA GPU runtime')
        require(policy['max_pair_tokens'] <= 32768 and policy['max_pair_tokens'] > 0
                and policy['truncation'] == 'reject' and policy['scoring'] == 'logit_yes_minus_logit_no'
                and policy['dtype'] == 'float16' and policy['attention'] == 'sdpa', 'Unsupported scoring policy')
        require(batch_size > 0 and padded_tokens >= policy['max_pair_tokens'], 'Invalid GPU budgets')
        self.torch, self.policy = torch, policy
        self.device = torch.device('cuda')
        self.batch_size, self.padded_tokens = batch_size, padded_tokens
        self.tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, revision=REVISION,
                                                       padding_side='left', trust_remote_code=False)
        self.model = AutoModelForCausalLM.from_pretrained(MODEL_ID, revision=REVISION,
            torch_dtype=torch.float16, attn_implementation='sdpa', trust_remote_code=False).to('cuda').eval()
        require(getattr(self.model.config, '_commit_hash', None) == REVISION, 'Loaded model revision differs')
        if self.tokenizer.pad_token_id is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token
        self.yes = self.tokenizer.encode('yes', add_special_tokens=False)
        self.no = self.tokenizer.encode('no', add_special_tokens=False)
        require(len(self.yes) == len(self.no) == 1 and self.yes != self.no, 'Unexpected yes/no tokenizer mapping')
        count = sum(p.numel() for p in self.model.parameters())
        require(0 < count <= 15_000_000_000 and policy['max_pair_tokens'] <= self.model.config.max_position_embeddings,
                'Model size/context exceeds allowed budget')
        self.registry = dict(model_id=MODEL_ID, revision=REVISION, commit_date=MODEL_DATE,
                             parameters=count, dtype='float16', scoring=policy['scoring'],
                             transformers=transformers.__version__, torch=torch.__version__,
                             tokenizer_revision=REVISION, gpu=torch.cuda.get_device_name(0))

    def score(self, query, passages):
        features = [encoded_pair(self.tokenizer, query, p, self.policy) for p in passages]
        scores = []
        index, budget = 0, self.batch_size
        while index < len(features):
            n = min(budget, len(features)-index)
            while n > 1 and max(len(v['input_ids']) for v in features[index:index+n])*n > self.padded_tokens:
                n -= 1
            try:
                result = self._batch_scores(features[index:index+n])
            except self.torch.cuda.OutOfMemoryError:
                self.torch.cuda.empty_cache()
                if n == 1:
                    raise
                budget = max(1, n//2)
                print(f'CUDA OOM: retry smaller batch={budget}', flush=True)
                continue
            scores.extend(result)
            index += n
        return scores

    def _batch_scores(self, features):
        inputs = self.tokenizer.pad(features, padding=True, return_tensors='pt')
        inputs = {k: v.to(self.device) for k, v in inputs.items()}
        with self.torch.inference_mode():
            # Keep only the last token logits: avoids full B x sequence x vocabulary allocation.
            logits = self.model(**inputs, use_cache=False, logits_to_keep=1).logits[:, -1, :].float()
            values = (logits[:, self.yes[0]] - logits[:, self.no[0]]).cpu().tolist()
        require(all(math.isfinite(s) for s in values), 'Nonfinite Qwen score')
        return values


def cache_key(query, text, policy):
    return signature(dict(query_sha256=digest(query.encode()), text_sha256=digest(text.encode()), policy=policy))


def verify_score_record(record, row, query, candidates, run_sig, policy):
    checked(record)
    require(record['id'] == row['id'] == query['id'] and record['run_signature'] == run_sig and
            record['query_sha256'] == digest(query['query'].encode()) and
            record['candidates_sha256'] == signature(candidates), 'Score query/input identity differs')
    registry = record['model_registry']
    require(registry['model_id'] == policy['model_id'] and registry['revision'] == policy['revision'] and
            registry['commit_date'] == policy['commit_date'] and
            registry['tokenizer_revision'] == policy['revision'] and registry['scoring'] == policy['scoring'] and
            registry['dtype'] == policy['dtype'] and type(registry['parameters']) is int and
            0 < registry['parameters'] <= 15_000_000_000, 'Score model registry differs')
    require(len(record['scores']) == len(candidates), 'Missing candidate scores')
    for c, score in zip(candidates, record['scores']):
        require(score['candidate_id'] == c['candidate_id'] and type(score['score']) in (int, float)
                and math.isfinite(score['score']), 'Invalid score rows')
    return [v['score'] for v in record['scores']]


def run_scoring(rows, queries, identity, output, local_cache, *, stop_after_queries=5,
                batch_size=8, padded_tokens=8192, scorer_factory=None):
    require(stop_after_queries is None or type(stop_after_queries) is int and stop_after_queries > 0,
            'Positive pilot limit or None required')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / 'manifest.json'
    run_sig = signature(identity)
    if manifest_path.exists():
        previous = checked(read(manifest_path))
        require(previous['identity'] == identity, 'Scoring inputs changed; use new run directory')
    else:
        require(not any(output.iterdir()), 'Nonempty run without manifest; use new directory')
    done, hashes, new_count, scored_pairs = [], {}, 0, 0
    scorer = None
    def save(state):
        write(manifest_path, signed(dict(identity=identity, run_signature=run_sig, state=state,
            completed_query_ids=done, score_files=hashes, queries=len(rows),
            new_queries_last_invocation=new_count, scored_unique_pairs_last_invocation=scored_pairs)))
    save('running')
    Path(local_cache).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(local_cache) as db:
        db.execute('CREATE TABLE IF NOT EXISTS scores(key TEXT PRIMARY KEY,score REAL NOT NULL)')
        try:
            for row, query in zip(rows, queries):
                candidates = make_candidates(row, identity['generation'])
                path = output / f"query-{row['id']}.json"
                if path.is_file():
                    verify_score_record(read(path), row, query, candidates, run_sig, identity['model_policy'])
                else:
                    keys, texts, missing = [], {}, []
                    for c in candidates:
                        text = evidence_text(row, c)
                        key = cache_key(query['query'], text, identity['model_policy'])
                        keys.append(key)
                        texts[key] = text
                    values = {}
                    for key in dict.fromkeys(keys):
                        hit = db.execute('SELECT score FROM scores WHERE key=?', (key,)).fetchone()
                        if hit:
                            require(math.isfinite(hit[0]), 'Nonfinite cached score')
                            values[key] = hit[0]
                        else:
                            missing.append(key)
                    if scorer is None:
                        scorer = (scorer_factory or QwenScorer)(identity['model_policy'],
                            batch_size=batch_size, padded_tokens=padded_tokens)
                    if missing:
                        scores = scorer.score(query['query'], [texts[k] for k in missing])
                        require(len(scores) == len(missing) and all(type(s) in (int, float) and math.isfinite(s) for s in scores),
                                'Scorer returned invalid scores')
                        for key, score in zip(missing, scores):
                            db.execute('INSERT INTO scores VALUES(?,?)', (key, float(score)))
                            values[key] = float(score)
                        db.commit()
                        scored_pairs += len(missing)
                    record = signed(dict(id=row['id'], run_signature=run_sig,
                        query_sha256=digest(query['query'].encode()), candidates_sha256=signature(candidates),
                        model_registry=scorer.registry,
                        scores=[dict(candidate_id=c['candidate_id'], score=values[k]) for c,k in zip(candidates,keys)]))
                    verify_score_record(record, row, query, candidates, run_sig, identity['model_policy'])
                    write(path, record)
                    new_count += 1
                done.append(row['id']); hashes[str(row['id'])] = file_hash(path)
                save('running')
                print(f"Query {row['id']}: {len(done)}/{len(rows)} complete; new this call={new_count}", flush=True)
                if stop_after_queries is not None and new_count >= stop_after_queries and len(done) < len(rows):
                    save('paused')
                    return read(manifest_path)
        except BaseException:
            save('interrupted')
            raise
    save('complete')
    return read(manifest_path)


def overlap_iou(a, b):
    if (a['doc_id'], a['parent_index']) != (b['doc_id'], b['parent_index']):
        return 0.0  # Source coordinates across different parent chunks are unavailable.
    overlap = max(0, min(a['end'], b['end']) - max(a['start'], b['start']))
    return overlap / (max(a['end'], b['end']) - min(a['start'], b['start']))


def select_evidence(row, candidates, scores, limits, originals_only=False):
    require(type(limits['max_chunks']) is int and limits['max_chunks'] > 0 and
            type(limits['per_doc']) is int and limits['per_doc'] > 0 and
            0 < limits['overlap_iou'] <= 1, 'Invalid selection limits')
    require(len(candidates) == len(scores) and all(math.isfinite(s) for s in scores), 'Invalid score array')
    selected, per_doc, decisions, texts = [], {}, [], {}
    ranked = sorted(zip(candidates, scores), key=lambda x: (-x[1], x[0]['candidate_id']))
    for c, score in ranked:
        text = evidence_text(row, c)
        reason = None
        if originals_only and c['kind'] != 'original':
            reason = 'subspan_disabled'
        elif len(selected) >= limits['max_chunks']:
            reason = 'global_cap'
        elif per_doc.get(c['doc_id'], 0) >= limits['per_doc']:
            reason = 'per_doc_cap'
        else:
            peers = [v for v in selected if v['doc_id'] == c['doc_id']]
            if any(text == texts[v['candidate_id']] for v in peers):
                reason = 'same_doc_exact_duplicate'
            elif any(overlap_iou(c, v) >= limits['overlap_iou'] for v in peers):
                reason = 'same_parent_overlap'
        if reason is None:
            selected.append(c)
            texts[c['candidate_id']] = text
            per_doc[c['doc_id']] = per_doc.get(c['doc_id'], 0)+1
        decisions.append(dict(candidate_id=c['candidate_id'], reason=reason or 'selected', score=score))
    prediction = dict(id=row['id'], relevant_docs=list(row['relevant_docs']), relevant_chunks=[
        dict(doc_id=c['doc_id'], chunk_text=texts[c['candidate_id']], chunk_order=i) for i,c in enumerate(selected)])
    return prediction, selected, decisions


def package_variants(rows, queries, identity, score_dir, destination, baseline_raw, variants):
    score_dir, destination = Path(score_dir), Path(destination)
    require(not destination.exists(), 'Choose a new export directory')
    require(digest(baseline_raw) == identity['inputs']['submission_json_sha256'], 'Baseline bytes differ')
    manifest = checked(read(score_dir / 'manifest.json'))
    require(manifest['identity'] == identity and manifest['state'] == 'complete' and
            manifest['completed_query_ids'] == [r['id'] for r in rows], 'Finish all scoring queries before export')
    run_sig = signature(identity)
    manifest_hash = file_hash(score_dir / 'manifest.json')
    destination.mkdir(parents=True)
    # S0 is byte-for-byte baseline JSON, repackaged without changing prediction data.
    with zipfile.ZipFile(destination / 'S0_baseline.zip', 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('submission.json', baseline_raw)
    predictions, counts = {v['name']: [] for v in variants}, {v['name']: [] for v in variants}
    require(len(predictions) == len(variants) and all(re.fullmatch(r'[A-Za-z0-9_-]+', v['name']) for v in variants),
            'Unique safe variant names required')
    evidence_sidecar = destination / 'EVIDENCE_PROVENANCE.jsonl'
    with evidence_sidecar.open('w', encoding='utf-8') as sidecar:
        for row, query in zip(rows, queries):
            candidates = make_candidates(row, identity['generation'])
            path = score_dir / f"query-{row['id']}.json"
            require(file_hash(path) == manifest['score_files'][str(row['id'])], 'Score file changed after completion')
            scores = verify_score_record(read(path), row, query, candidates, run_sig, identity['model_policy'])
            for variant in variants:
                pred, selected, decisions = select_evidence(row, candidates, scores, variant['limits'],
                                                           variant['originals_only'])
                require(pred['relevant_docs'] == row['relevant_docs'], 'Document list changed')
                predictions[variant['name']].append(pred)
                before = {(c['doc_id'], c['chunk_text']) for c in row['relevant_chunks']}
                after = {(c['doc_id'], c['chunk_text']) for c in pred['relevant_chunks']}
                counts[variant['name']].append(dict(id=row['id'], chunks=len(selected),
                    changed_evidence=before != after, subspans=sum(c['kind']=='subspan' for c in selected),
                    documents_with_chunks=len({c['doc_id'] for c in selected})))
                sidecar.write(canonical(dict(variant=variant['name'], id=row['id'], selected=selected,
                                             decisions=decisions))+'\n')
    require(file_hash(score_dir / 'manifest.json') == manifest_hash, 'Scoring manifest changed during export')
    exports = []
    for variant in variants:
        name = variant['name']
        payload = canonical(predictions[name]).encode()
        archive = destination / f'{name}.zip'
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr('submission.json', payload)
        with zipfile.ZipFile(archive) as z:
            require(z.namelist() == ['submission.json'] and z.read('submission.json') == payload, 'ZIP differs')
        exports.append(dict(variant=variant, zip=archive.name, zip_sha256=file_hash(archive),
            json_sha256=digest(payload), queries=len(rows),
            changed_queries=sum(c['changed_evidence'] for c in counts[name]),
            total_chunks=sum(c['chunks'] for c in counts[name]),
            total_subspans=sum(c['subspans'] for c in counts[name]), query_statistics=counts[name]))
    report = dict(validation='passed', validation_scope='input submission subset/slices and score integrity',
        original_corpus_evidence_verified=False, official_doc_allowlist_verified=False,
        relevance_measured=False, official_lcs_evaluated=False, source_identity=identity,
        score_manifest_sha256=manifest_hash, baseline_json_sha256=digest(baseline_raw),
        provenance_sha256=file_hash(evidence_sidecar), exports=exports)
    write(destination / 'REPORT.json', report)
    return report
