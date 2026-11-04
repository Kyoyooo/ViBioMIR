"""Source-bound BM25 + three dense retrievers, two rerankers and submission export.

Drive checkpoints contain source/model/code identities. No submission ZIP is an input.
"""
from collections import Counter, defaultdict
import gzip
import io
import json
import math
from pathlib import Path
import re
import shutil
import sqlite3
import time
import zipfile

try:
    from . import qwen_submission_trial as core
    from . import multimodel_evidence as multi
    from . import stage9_dense_recovery as recovery
except ImportError:
    import qwen_submission_trial as core
    import multimodel_evidence as multi
    import stage9_dense_recovery as recovery

SOURCE = dict(signature='5ce3d7c02e6a0a8275e203a4cff9c12fb371fc8cf0fd9515574250acfff3751c',
    manifest_sha256='d88fd1c046a93950c7e97ef5c8786d933a677ef1f54e7880f5c34fa6c4ff40f1',
    validation_sha256='5bdc88900f47989a4edb653ae1f6b90ba90a79760f620edb35ac09a48d20d993',
    documents=855745, chunks=5512624, batches=1712)
LINKS_SHA = '87e44a792d7a99fb0fde5dff7f09f1a38a0a1fdf02ddd1ac93a005736e00b0a7'
BRANCHES = ('bm25', 'bge', 'qwen', 'e5')


def embedding_policies(qwen_attention='sdpa'):
    core.require(qwen_attention in ('sdpa', 'eager'), 'Use sdpa or eager Qwen attention')
    return dict(
        bge=dict(model_id='BAAI/bge-m3', revision='5617a9f61b028005a4858fdac845db406aefb181',
                 date='2024-07-03T14:50:10Z', pooling='cls', max_tokens=1024, safetensors=False),
        qwen=dict(model_id='Qwen/Qwen3-Embedding-0.6B', revision='97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3',
                  date='2026-04-20T02:45:58Z', pooling='last', max_tokens=2048,
                  padding='left', attention=qwen_attention, dtype='bfloat16_native_else_float16'),
        e5=dict(model_id='intfloat/multilingual-e5-large', revision='3d7cfbdacd47fdda877c5cd8a79fbcc4f2a574f3',
                date='2026-04-02T02:16:59Z', pooling='mean', max_tokens=512))


def make_plan(query_path, queries, query_limit=20, batch_limit=4, top_k=500, rerank_k=400, qwen_attention='sdpa'):
    import pyarrow.parquet as pq
    core.require(query_limit is None or type(query_limit) is int and query_limit > 0, 'Invalid query limit')
    core.require(batch_limit is None or type(batch_limit) is int and 0 < batch_limit <= SOURCE['batches'], 'Invalid batch limit')
    core.require(type(top_k) is int and type(rerank_k) is int and 0 < rerank_k <= top_k, 'Invalid pool sizes')
    core.require(core.file_hash(query_path) == core.EXPECTED_QUERY_SHA256 and len(queries) == 1200,
                 'Use the original official 1200-query snapshot')
    core.require(queries == pq.read_table(query_path, columns=['id', 'query']).to_pylist(), 'Query text/order differs from file')
    selected = queries if query_limit is None else queries[:query_limit]
    return core.signed(dict(operation='corpus-multiretrieval-v1', source=SOURCE,
        query_sha256=core.file_hash(query_path), queries=selected,
        batch_indices=list(range(SOURCE['batches'] if batch_limit is None else batch_limit)),
        full_scope=len(selected) == 1200 and batch_limit is None,
        top_k=top_k, rerank_k=rerank_k, embeddings=embedding_policies(qwen_attention), rerankers=multi.policies(),
        dense_scoring='normalized-float32-inner-product', long_text='overlap64-token-windows-weighted-mean-v1',
        bm25='sqlite-fts5-global-bm25-unicode61-plus-cjk-bigrams-v1', bm25_query_terms=128,
        code={name: core.file_hash(path) for name, path in dict(
            pipeline=__file__, core=core.__file__, multi=multi.__file__, recovery=recovery.__file__).items()}))


def check_plan(plan):
    core.checked(plan)
    core.require(plan['source'] == SOURCE and plan['embeddings'] == embedding_policies(plan['embeddings']['qwen']['attention'])
        and plan['rerankers'] == multi.policies(), 'Source/model policy changed')
    core.require(plan['batch_indices'] == list(range(len(plan['batch_indices']))) and
        0 < len(plan['batch_indices']) <= SOURCE['batches'], 'Invalid source scope')
    core.require(plan['full_scope'] == (len(plan['queries']) == 1200 and
        len(plan['batch_indices']) == SOURCE['batches']), 'Full/pilot scope differs')
    for name, path in dict(pipeline=__file__, core=core.__file__, multi=multi.__file__, recovery=recovery.__file__).items():
        core.require(plan['code'][name] == core.file_hash(path), 'Support code changed; use a new run')


def setup_source(directory, plan):
    check_plan(plan)
    source_plan, members, _ = recovery.inspect_bundles(directory, plan['source'])
    return source_plan, members


def source_batch(source_plan, members, index):
    part = source_plan['parts'][index]
    archive, prefix, _ = members[index]
    with zipfile.ZipFile(archive) as z:
        raw = z.read(prefix + 'documents.jsonl.gz')
    core.require(core.digest(raw) == part['source_sha256'], f'Source batch changed: {index}')
    rows, documents = [], 0
    with gzip.open(io.BytesIO(raw), 'rt', encoding='utf-8') as stream:
        for row_index, line in enumerate(stream):
            document = json.loads(line)
            documents += 1
            for order, chunk in enumerate(document['chunks']):
                core.require(chunk['doc_id'] == document['doc_id'] and
                    chunk['derived_version_id'] == document['derived_version_id'] and
                    core.digest(chunk['chunk_text'].encode()) == chunk['text_sha256'], 'Source evidence checksum differs')
                core.require(type(chunk['doc_id']) is int and bool(chunk['retrieval_text'].strip())
                    and bool(chunk['chunk_text'].strip()), 'Invalid source text/ID')
                rows.append({**chunk, 'source_batch': index, 'source_row': row_index, 'source_chunk_order': order})
    core.require(len(rows) == part['rows'] and documents == part['documents'], 'Source counts differ')
    return rows


def assigned(plan, shard, shards):
    core.require(type(shards) is int and 1 <= shards <= 32 and type(shard) is int and 0 <= shard < shards, 'Invalid shard')
    return [i for i in plan['batch_indices'] if i % shards == shard]


def branch_root(run, role, shard, shards):
    return Path(run) / 'retrieval' / role / f'part-{shard}-of-{shards}'


def trim(hits, k):
    return dict(sorted(hits.items(), key=lambda p: (-p[1], p[0]))[:k])


def lexical(text):
    # Preserve Vietnamese words/numbers. Add Chinese unigrams and adjacent bigrams.
    return ' '.join(re.findall(r'[^\W_]+', re.sub(r'[\u3400-\u9fff]+', ' ', text.lower()), flags=re.UNICODE)
        + [token for run in re.findall(r'[\u3400-\u9fff]+', text)
           for token in list(run) + [run[i:i+2] for i in range(len(run)-1)]])


def validate_embedding_batch(batch, vocabulary_size, max_tokens):
    """Check IDs and gather positions on CPU before any CUDA indexing."""
    import torch
    ids, mask = batch['input_ids'], batch['attention_mask']
    core.require(ids.device.type == mask.device.type == 'cpu', 'Validate token batch before CUDA transfer')
    core.require(ids.ndim == 2 and ids.shape == mask.shape and 0 < ids.shape[1] <= max_tokens,
                 'Invalid embedding token/mask shape or context length')
    core.require(ids.dtype in (torch.int64, torch.int32), 'Token IDs must be integers')
    core.require(int(ids.min()) >= 0 and int(ids.max()) < vocabulary_size,
        f'Token IDs outside model vocabulary: min={int(ids.min())}, max={int(ids.max())}, vocabulary={vocabulary_size}')
    core.require(bool(((mask == 0) | (mask == 1)).all()) and bool((mask.sum(1) > 0).all()),
                 'Attention mask must be binary with nonempty sequences')
    return [int(row.nonzero()[-1]) for row in mask]


def pool_embeddings(hidden, mask, pooling, last_positions):
    import torch
    core.require(hidden.ndim == 3 and hidden.shape[:2] == mask.shape, 'Hidden state and mask shape differ')
    if pooling == 'cls':
        return hidden[:, 0]
    if pooling == 'mean':
        return (hidden.float()*mask[..., None]).sum(1)/mask.sum(1)[:, None]
    core.require(pooling == 'last' and len(last_positions) == hidden.shape[0] and
        all(0 <= i < hidden.shape[1] for i in last_positions), 'Invalid last-token pooling positions')
    positions = torch.tensor(last_positions, device=hidden.device, dtype=torch.long)
    return hidden.gather(1, positions[:, None, None].expand(-1, 1, hidden.shape[2])).squeeze(1)


def embedding_dtype(torch, policy):
    # PyTorch's default includes emulation; tensor creation is not native BF16 compute.
    native_bf16 = torch.cuda.get_device_capability()[0] >= 8 and torch.cuda.is_bf16_supported(including_emulation=False)
    return torch.bfloat16 if policy.get('dtype', '').startswith('bfloat16') and native_bf16 else torch.float16


def sorted_windows(features, owners, weights):
    order = sorted(range(len(features)), key=lambda i: len(features[i]['input_ids']))
    return ([features[i] for i in order], [owners[i] for i in order], [weights[i] for i in order])


def window_batch_size(features, offset, max_batch, padded_tokens):
    size = min(max_batch, len(features)-offset)
    while size > 1 and size*max(len(f['input_ids']) for f in features[offset:offset+size]) > padded_tokens:
        size -= 1
    return size


class Encoder:
    """Native model pooling; all tokens covered by windows, never silent truncation."""
    def __init__(self, policy, batch_size=32, padded_tokens=8192):
        import numpy as np
        import torch
        from transformers import AutoModel, AutoTokenizer
        core.require(torch.cuda.is_available(), 'Embedding worker requires CUDA')
        core.require(type(batch_size) is int and batch_size > 0 and padded_tokens >= policy['max_tokens'], 'Invalid encoding batch/token budgets')
        self.np, self.torch, self.policy, self.batch_size = np, torch, policy, batch_size
        self.padded_tokens = padded_tokens
        self.debug_cuda = __import__('os').environ.get('CUDA_LAUNCH_BLOCKING') == '1'
        self.tokenizer = AutoTokenizer.from_pretrained(policy['model_id'], revision=policy['revision'],
            trust_remote_code=False, padding_side=policy.get('padding', 'right'))
        dtype = embedding_dtype(torch, policy)
        self.model = AutoModel.from_pretrained(policy['model_id'], revision=policy['revision'],
            trust_remote_code=False, dtype=dtype, use_safetensors=policy.get('safetensors', True),
            attn_implementation=policy.get('attention', 'sdpa')).cuda().eval()
        if self.tokenizer.pad_token_id is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token
        core.require(self.model.config._commit_hash == policy['revision'], 'Embedding revision differs')
        core.require(sum(p.numel() for p in self.model.parameters()) <= 15_000_000_000, 'Model exceeds parameter limit')
        self.vocabulary_size = self.model.get_input_embeddings().num_embeddings
        print('Embedding runtime:', dict(model=policy['model_id'], gpu=torch.cuda.get_device_name(0),
            torch=torch.__version__, cuda=torch.version.cuda, dtype=str(dtype),
            attention=policy.get('attention', 'sdpa'), padding=self.tokenizer.padding_side,
            vocabulary=self.vocabulary_size, batch_size=batch_size, padded_tokens=padded_tokens), flush=True)

    def encode(self, texts, query=False):
        import numpy as np
        t, tok, policy = self.torch, self.tokenizer, self.policy
        features, owners, weights = [], [], []
        for owner, text in enumerate(texts):
            prefix = ''
            if policy['pooling'] == 'mean':
                prefix = 'query: ' if query else 'passage: '
            elif policy['pooling'] == 'last' and query:
                prefix = 'Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery: '
            header = tok.encode(prefix, add_special_tokens=False)
            body = tok.encode(text, add_special_tokens=False)
            capacity = policy['max_tokens'] - len(header) - tok.num_special_tokens_to_add(pair=False)
            core.require(capacity > 64 and body, 'Invalid token budget/empty text')
            start = 0
            while start < len(body):
                end = min(start+capacity, len(body))
                ids = tok.build_inputs_with_special_tokens(header+body[start:end])
                features.append(dict(input_ids=ids, attention_mask=[1]*len(ids)))
                owners.append(owner)
                weights.append(end-start if start == 0 else end-start-64)
                if end == len(body):
                    break
                start = end-64
        features, owners, weights = sorted_windows(features, owners, weights)
        output, pos = None, 0
        while pos < len(features):
            size = window_batch_size(features, pos, self.batch_size, self.padded_tokens)
            stage = 'CPU token validation'
            try:
                batch = tok.pad(features[pos:pos+size], padding=True, return_tensors='pt')
                last_positions = validate_embedding_batch(batch, self.vocabulary_size, policy['max_tokens'])
                stage = 'CUDA transfer / model forward'
                batch = batch.to('cuda')
                with t.inference_mode():
                    kwargs = dict(use_cache=False) if policy['pooling'] == 'last' else {}
                    hidden = self.model(**batch, **kwargs).last_hidden_state
                    if self.debug_cuda:
                        t.cuda.synchronize()
                    stage = 'token pooling'
                    mask = batch['attention_mask']
                    vectors = pool_embeddings(hidden, mask, policy['pooling'], last_positions).float().cpu().numpy()
                    stage = 'CPU vector normalization'
                    core.require(np.isfinite(vectors).all(), 'Model returned nonfinite embeddings')
                    lengths = np.linalg.norm(vectors, axis=1, keepdims=True)
                    core.require(bool((lengths > 0).all()), 'Model returned zero embeddings')
                    vectors = vectors/lengths
                if output is None:
                    output = np.zeros((len(texts), vectors.shape[1]), dtype=np.float32)
                for j, vector in enumerate(vectors):
                    output[owners[pos+j]] += vector*weights[pos+j]
                pos += size
            except t.cuda.OutOfMemoryError:
                if size == 1:
                    raise
                self.batch_size = max(1, size//2)
                batch, hidden, vectors = None, None, None
                t.cuda.empty_cache()
            except RuntimeError as exc:
                if 'device-side assert' not in str(exc) and 'CUDA error' not in str(exc):
                    raise
                # Do not call CUDA again: a device assertion poisons this context.
                raise RuntimeError(f'Embedding failed at {stage}; model={policy["model_id"]}, '
                    f'query={query}, window_offset={pos}, batch_size={size}. '
                    'Restart the Colab session before retrying. Set DEBUG_CUDA=True before importing torch '
                    'for synchronous diagnostics. Original error: '+str(exc)) from exc
        norms = np.linalg.norm(output, axis=1, keepdims=True)
        core.require(np.all(norms > 0) and np.isfinite(output).all(), 'Invalid embedding vectors')
        return output/norms


def dense_top(query_vectors, vectors, k):
    import numpy as np
    # Per-source-batch exact cosine search. Bounded CPU RAM; use GPU if available.
    k = min(k, len(vectors))
    if not k:
        return [[] for _ in query_vectors]
    import torch
    result = []
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    bank = torch.as_tensor(vectors, dtype=torch.float32, device=device)
    for start in range(0, len(query_vectors), 32):
        q = torch.as_tensor(query_vectors[start:start+32], dtype=torch.float32, device=device)
        scores = (q @ bank.T).cpu().numpy()
        for row in scores:
            # Stable source order resolves score ties.
            indices = np.argsort(-row, kind='stable')[:k]
            result.append([(int(i), float(row[i])) for i in indices])
    return result


def retrieve_dense(plan, run, role, source_plan, members, shard=0, shards=1,
                   stop_after_batches=2, save_embeddings=True, encoder_factory=Encoder):
    import numpy as np
    check_plan(plan)
    core.require(role in plan['embeddings'], 'Unknown dense role')
    parts = assigned(plan, shard, shards)
    core.require(parts, 'Empty shard; reduce SHARD_COUNT')
    output = branch_root(run, role, shard, shards)
    output.mkdir(parents=True, exist_ok=True)
    identity = dict(plan=plan['signature'], role=role, shard=shard, shards=shards,
                    save_embeddings=save_embeddings)
    checkpoint = output/'checkpoint.json'
    saved = core.checked(core.read(checkpoint)) if checkpoint.exists() else None
    if saved:
        core.require(saved['identity'] == identity and saved['completed_batches'] == parts[:len(saved['completed_batches'])], 'Retrieval checkpoint identity/coverage differs')
    completed = saved['completed_batches'] if saved else []
    hits = saved['hits'] if saved else [{} for _ in plan['queries']]
    core.require(len(hits) == len(plan['queries']), 'Query results differ')
    encoder = None
    def encode(texts, query=False):
        nonlocal encoder
        if encoder is None:
            encoder = encoder_factory(plan['embeddings'][role])
        return encoder.encode(texts, query=query)
    new = 0
    qvectors = None
    for index in parts[len(completed):]:
        if stop_after_batches is not None and new >= stop_after_batches:
            break
        started = time.monotonic()
        timing = dict(batch=index)
        rows = source_batch(source_plan, members, index)
        timing['source_seconds'] = round(time.monotonic()-started, 3)
        if not rows:
            completed.append(index)
            continue
        if qvectors is None:
            print(f'{role}: loading encoder / encoding {len(plan["queries"])} queries (once this session)', flush=True)
            query_started = time.monotonic()
            qvectors = encode([q['query'] for q in plan['queries']], query=True)
            timing['query_setup_seconds'] = round(time.monotonic()-query_started, 3)
        encode_started = time.monotonic()
        vector_path = output/f'embeddings-{index:06d}.npy'
        receipt_path = output/f'embeddings-{index:06d}.json'
        if vector_path.is_file() and receipt_path.is_file():
            receipt = core.checked(core.read(receipt_path))
            core.require(receipt['identity'] == identity and receipt['batch'] == index and
                receipt['sha256'] == core.file_hash(vector_path), 'Embedding receipt differs')
            vectors = np.load(vector_path, allow_pickle=False)
        else:
            print(f'{role}: encoding source batch {index}, {len(rows)} chunks', flush=True)
            vectors = encode([row['retrieval_text'] for row in rows])
            if save_embeddings:
                temporary = vector_path.with_suffix('.npy.tmp')
                with temporary.open('wb') as stream:
                    np.save(stream, vectors, allow_pickle=False)
                temporary.replace(vector_path)
                core.write(receipt_path, core.signed(dict(identity=identity, batch=index,
                    sha256=core.file_hash(vector_path))))
        timing['vectors_load_or_encode_and_save_seconds'] = round(time.monotonic()-encode_started, 3)
        core.require(vectors.shape == (len(rows), qvectors.shape[1]) and np.isfinite(vectors).all(), 'Embedding shape/value differs')
        search_started = time.monotonic()
        for q, result in enumerate(dense_top(qvectors, vectors, plan['top_k'])):
            for position, score in result:
                key = rows[position]['chunk_id']
                hits[q][key] = max(score, hits[q].get(key, -float('inf')))
            hits[q] = trim(hits[q], plan['top_k'])
        timing['search_seconds'] = round(time.monotonic()-search_started, 3)
        completed.append(index)
        new += 1
        # Exact prefix is saved after each batch; no gzip timestamps in identity.
        backup_started = time.monotonic()
        core.write(checkpoint, core.signed(dict(identity=identity, completed_batches=completed, hits=hits)))
        timing['checkpoint_seconds'] = round(time.monotonic()-backup_started, 3)
        timing['total_seconds'] = round(time.monotonic()-started, 3)
        core.write(output/f'timing-{index:06d}.json', timing)
        print(f'{role} shard {shard}/{shards}: {len(completed)}/{len(parts)} batches; {timing}', flush=True)
    result = core.signed(dict(identity=identity, state='complete' if completed == parts else 'paused',
        completed_batches=completed, hits=hits))
    core.write(output/'result.json', result)
    return result


def retrieve_bm25(plan, run, source_plan, members, local_db, stop_after_batches=2):
    check_plan(plan)
    # One global FTS index: shard-local BM25 scores are not comparable.
    output = branch_root(run, 'bm25', 0, 1)
    output.mkdir(parents=True, exist_ok=True)
    destination = output/'bm25.sqlite3'
    local_db = Path(local_db)
    local_db.parent.mkdir(parents=True, exist_ok=True)
    if not local_db.exists() and destination.exists():
        shutil.copy2(destination, local_db)
    with sqlite3.connect(local_db) as db:
        db.execute('CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT)')
        old = db.execute('SELECT value FROM metadata WHERE key=?', ('plan',)).fetchone()
        core.require(old is None or old[0] == plan['signature'], 'Local BM25 database belongs to another plan')
        db.execute('INSERT OR IGNORE INTO metadata VALUES(?,?)', ('plan', plan['signature']))
        db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS passages USING fts5(chunk_id UNINDEXED,text,tokenize='unicode61 remove_diacritics 2')")
        db.execute('CREATE TABLE IF NOT EXISTS batches(id INTEGER PRIMARY KEY)')
        completed = [row[0] for row in db.execute('SELECT id FROM batches ORDER BY id')]
        core.require(completed == plan['batch_indices'][:len(completed)], 'BM25 batch prefix differs')
        new = 0
        for index in plan['batch_indices'][len(completed):]:
            if stop_after_batches is not None and new >= stop_after_batches:
                break
            rows = source_batch(source_plan, members, index)
            with db:
                db.executemany('INSERT INTO passages(chunk_id,text) VALUES(?,?)',
                    [(row['chunk_id'], lexical(row['retrieval_text'])) for row in rows])
                db.execute('INSERT INTO batches VALUES(?)', (index,))
            completed.append(index)
            new += 1
            print(f'BM25: {len(completed)}/{len(plan["batch_indices"])} batches', flush=True)
        hits = []
        if completed == plan['batch_indices']:
            for query in plan['queries']:
                terms = list(dict.fromkeys(lexical(query['query']).split()))[:plan['bm25_query_terms']]
                expression = ' OR '.join('"'+term.replace('"', '""')+'"' for term in terms)
                results = db.execute('SELECT chunk_id,bm25(passages) AS score FROM passages WHERE passages MATCH ? ORDER BY score,chunk_id LIMIT ?',
                    (expression, plan['top_k'])).fetchall() if terms else []
                hits.append(dict((key, -float(score)) for key, score in results))
    # Snapshot only at cell boundary. Each local batch transaction is resumable.
    pending = destination.with_suffix('.sqlite3.tmp')
    shutil.copy2(local_db, pending)
    core.require(core.file_hash(pending) == core.file_hash(local_db), 'BM25 backup differs')
    pending.replace(destination)
    result = core.signed(dict(identity=dict(plan=plan['signature'], role='bm25', shard=0, shards=1),
        state='complete' if completed == plan['batch_indices'] else 'paused', completed_batches=completed, hits=hits))
    core.write(output/'result.json', result)
    return result


def collect_branch(plan, run, role):
    check_plan(plan)
    files = sorted((Path(run)/'retrieval'/role).glob('part-*/result.json'))
    core.require(files, f'No retrieval results for {role}')
    hits, batches, shard_count = [dict() for _ in plan['queries']], set(), None
    for path in files:
        result = core.checked(core.read(path))
        identity = result['identity']
        core.require(identity['plan'] == plan['signature'] and identity['role'] == role
            and result['state'] == 'complete', f'Incomplete/wrong retrieval worker: {path}')
        core.require(path.parent == branch_root(run, role, identity['shard'], identity['shards']), 'Wrong shard directory')
        core.require(shard_count is None or shard_count == identity['shards'], 'Mixed shard counts; use a new plan directory')
        shard_count = identity['shards']
        expected = assigned(plan, identity['shard'], shard_count)
        core.require(result['completed_batches'] == expected and not batches.intersection(expected), 'Missing/duplicate source coverage')
        batches.update(expected)
        core.require(len(result['hits']) == len(hits), 'Query results missing')
        for q, values in enumerate(result['hits']):
            core.require(all(isinstance(s, (int, float)) and math.isfinite(s) for s in values.values()), 'Invalid retrieval scores')
            for key, score in values.items():
                hits[q][key] = max(score, hits[q].get(key, -float('inf')))
            hits[q] = trim(hits[q], plan['top_k'])
    core.require(batches == set(plan['batch_indices']), f'Missing {role} shards')
    return hits


def fuse_candidates(branches, k):
    scores, provenance = defaultdict(float), defaultdict(dict)
    for branch, hits in branches.items():
        for rank, (key, score) in enumerate(sorted(hits.items(), key=lambda p: (-p[1], p[0])), 1):
            scores[key] += 1/(60+rank)
            provenance[key][branch] = dict(rank=rank, score=score)
    return [dict(chunk_id=key, retrieval_rrf=score, branches=provenance[key]) for key, score in
            sorted(scores.items(), key=lambda p: (-p[1], p[0]))[:k]]


def prepare_pool(plan, run, source_plan, members, local_db):
    branch_hits = {role: collect_branch(plan, run, role) for role in BRANCHES}
    pools = [fuse_candidates({role: hits[q] for role, hits in branch_hits.items()}, plan['rerank_k'])
             for q in range(len(plan['queries']))]
    diagnostics = []
    for index, (query, pool) in enumerate(zip(plan['queries'], pools)):
        sets = {role: set(hits[index]) for role, hits in branch_hits.items()}
        diagnostics.append(dict(id=query['id'], union_candidates=len(set().union(*sets.values())),
            selected_for_rerank=len(pool), branches={role: dict(retrieved=len(keys),
                exclusive_candidates=len(keys-set().union(*(other for name, other in sets.items() if name != role))),
                represented_in_rerank_pool=sum(role in c['branches'] for c in pool)) for role, keys in sets.items()}))
    wanted = {item['chunk_id'] for pool in pools for item in pool}
    local_db = Path(local_db)
    local_db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(local_db) as db:
        db.execute('CREATE TABLE IF NOT EXISTS evidence(id TEXT PRIMARY KEY,payload TEXT)')
        db.execute('DELETE FROM evidence')
        for index in plan['batch_indices']:
            for row in source_batch(source_plan, members, index):
                if row['chunk_id'] in wanted:
                    db.execute('INSERT INTO evidence VALUES(?,?)', (row['chunk_id'], core.canonical(row)))
            if index % 25 == 0:
                print(f'Hydrate original evidence: batch {index}', flush=True)
        db.commit()
        core.require(db.execute('SELECT COUNT(*) FROM evidence').fetchone()[0] == len(wanted), 'Candidate missing from source')
        output, hashes = Path(run)/'prepared', {}
        output.mkdir(parents=True, exist_ok=True)
        for query, pool in zip(plan['queries'], pools):
            candidates = []
            for item in pool:
                raw = db.execute('SELECT payload FROM evidence WHERE id=?', (item['chunk_id'],)).fetchone()
                candidates.append({**json.loads(raw[0]), **item})
            core.require(candidates, f'No candidates for query {query["id"]}')
            path = output/f'query-{query["id"]}.json'
            core.write(path, core.signed(dict(plan=plan['signature'], **query, candidates=candidates)))
            hashes[str(query['id'])] = core.file_hash(path)
    manifest = core.signed(dict(plan=plan['signature'], state='complete', files=hashes,
        original_evidence_verified=True, full_scope=plan['full_scope'], queries=len(pools)))
    core.write(output/'manifest.json', manifest)
    core.write(output/'RETRIEVAL_DIAGNOSTICS.json', diagnostics)
    return manifest


def packet(plan, run, query):
    manifest = core.checked(core.read(Path(run)/'prepared/manifest.json'))
    path = Path(run)/'prepared'/f'query-{query["id"]}.json'
    core.require(manifest['plan'] == plan['signature'] and manifest['original_evidence_verified']
        and manifest['files'][str(query['id'])] == core.file_hash(path), 'Prepared evidence changed')
    value = core.checked(core.read(path))
    core.require(value['plan'] == plan['signature'] and value['query'] == query['query'] and value['id'] == query['id'], 'Query/packet differs')
    return value


def rerank(plan, run, role, shard=0, shards=1, stop_after_queries=5, scorer_factory=multi.ModelScorer):
    check_plan(plan)
    core.require(role in ('qwen', 'bge') and 0 <= shard < shards <= 32, 'Invalid rerank role/shard')
    output = Path(run)/'rerank'/role
    output.mkdir(parents=True, exist_ok=True)
    scorer, completed, new = None, [], 0
    for index, query in enumerate(plan['queries']):
        if index % shards != shard:
            continue
        item = packet(plan, run, query)
        path = output/f'query-{query["id"]}.json'
        if path.exists():
            score_record(plan, item, core.read(path), role)
            completed.append(query['id'])
            continue
        if stop_after_queries is not None and new >= stop_after_queries:
            break
        if scorer is None:
            scorer = scorer_factory(plan['rerankers'][role])
        scores = scorer.score(query['query'], [c['retrieval_text'] for c in item['candidates']])
        record = core.signed(dict(plan=plan['signature'], packet_signature=item['signature'], role=role,
            model=plan['rerankers'][role], id=query['id'], scores=scores))
        score_record(plan, item, record, role)
        core.write(path, record)
        completed.append(query['id'])
        new += 1
        print(f'Rerank {role}: query {query["id"]}, {len(scores)} candidates', flush=True)
    return dict(role=role, shard=shard, shards=shards, completed_ids=completed, new_queries=new)


def score_record(plan, item, value, role):
    core.checked(value)
    core.require(value['plan'] == plan['signature'] and value['packet_signature'] == item['signature']
        and value['role'] == role and value['id'] == item['id'] and value['model'] == plan['rerankers'][role], 'Rerank identity differs')
    core.require(len(value['scores']) == len(item['candidates']) and
        all(type(s) in (int, float) and math.isfinite(s) for s in value['scores']), 'Invalid rerank scores')
    return value['scores']


def choose(item, scores, max_docs=120, max_chunks=120, per_doc=6):
    ranked = sorted(range(len(scores)), key=lambda i: (-scores[i], item['candidates'][i]['chunk_id']))
    docs, selected, seen, counts, versions = [], [], set(), Counter(), {}
    for i in ranked:
        c = item['candidates'][i]
        d = c['doc_id']
        if d not in docs and len(docs) < max_docs:
            docs.append(d)
    for i in ranked:
        c = item['candidates'][i]
        d = c['doc_id']
        key = (d, c['text_sha256'])
        if d not in docs or len(selected) >= max_chunks or counts[d] >= per_doc or key in seen:
            continue
        if d in versions and versions[d] != c['derived_version_id']:
            continue
        overlap = False
        for peer in selected:
            if (d, c['derived_version_id'], c.get('region_id')) != (peer['doc_id'], peer['derived_version_id'], peer.get('region_id')):
                continue
            if all(k in c and k in peer for k in ('source_start', 'source_end')):
                intersection = max(0, min(c['source_end'], peer['source_end'])-max(c['source_start'], peer['source_start']))
                union = max(c['source_end'], peer['source_end'])-min(c['source_start'], peer['source_start'])
                if union > 0 and intersection/union >= .8:
                    overlap = True
                    break
        if overlap:
            continue
        selected.append(c)
        seen.add(key)
        counts[d] += 1
        versions[d] = c['derived_version_id']
    prediction = dict(id=item['id'], relevant_docs=docs, relevant_chunks=[
        dict(doc_id=c['doc_id'], chunk_text=c['chunk_text'], chunk_order=i) for i, c in enumerate(selected)])
    return prediction, selected


def merge_export(plan, run, destination, allowed_ids=None):
    check_plan(plan)
    destination = Path(destination)
    core.require(not destination.exists(), 'Use a new output directory')
    core.require(not plan['full_scope'] or allowed_ids is not None, 'Full submission requires official document allowlist')
    predictions, provenance = [], []
    for query in plan['queries']:
        item = packet(plan, run, query)
        ranks = defaultdict(float)
        for role in ('qwen', 'bge'):
            path = Path(run)/'rerank'/role/f'query-{query["id"]}.json'
            core.require(path.is_file(), f'Finish {role} reranking query {query["id"]}')
            scores = score_record(plan, item, core.read(path), role)
            for rank, i in enumerate(sorted(range(len(scores)), key=lambda i: (-scores[i], item['candidates'][i]['chunk_id'])), 1):
                ranks[i] += 1/(60+rank)
        pred, selected = choose(item, [ranks[i] for i in range(len(item['candidates']))])
        core.require(pred['relevant_chunks'], 'Empty evidence prediction')
        if allowed_ids is not None:
            core.require(set(pred['relevant_docs']) <= allowed_ids, 'Document ID absent from official links')
        predictions.append(pred)
        provenance.append(dict(id=query['id'], selected=[{k: c.get(k) for k in (
            'chunk_id', 'doc_id', 'derived_version_id', 'source_batch', 'source_row', 'source_chunk_order',
            'region_id', 'source_start', 'source_end', 'text_sha256', 'branches')} for c in selected]))
    destination.mkdir(parents=True)
    full = plan['full_scope']
    if full:
        core.require(allowed_ids is not None and len(predictions) == 1200, 'Full submission requires official document allowlist')
        payload = core.canonical(predictions).encode()
        (destination/'submission.json').write_bytes(payload)
        with zipfile.ZipFile(destination/'submission.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('submission.json', payload)
    else:
        core.write(destination/'PILOT_PREDICTIONS.json', predictions)
    with (destination/'EVIDENCE_PROVENANCE.jsonl').open('w', encoding='utf-8') as stream:
        for row in provenance:
            stream.write(core.canonical(row)+'\n')
    report = dict(state='full_submission_exported' if full else 'pilot_only_not_for_submission',
        plan_signature=plan['signature'], queries=len(predictions), source_batches=len(plan['batch_indices']),
        original_evidence_verified=True, official_ids_verified=allowed_ids is not None,
        relevance_measured=False, official_lcs_measured=False,
        documents=sum(len(p['relevant_docs']) for p in predictions), chunks=sum(len(p['relevant_chunks']) for p in predictions))
    if full:
        report['submission_zip_sha256'] = core.file_hash(destination/'submission.zip')
        report['submission_json_sha256'] = core.file_hash(destination/'submission.json')
    core.write(destination/'REPORT.json', report)
    return report
