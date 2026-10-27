"""Owner-run S06 chapter metadata with immutable batches and source-bound resume."""
import argparse
from collections import Counter
from contextlib import contextmanager
import fcntl
from itertools import zip_longest
import json
from pathlib import Path
import time

from .chunk_batches import batches, commit_batch, finish, output_store, read_json, read_rows
from .chunk_colab import restore_checkpoint, save_checkpoint
from .icd import (CHAPTERS, RuleTagger, annotate, bounded_segments, evidence, fields,
                  load_taxonomy, prototypes, require, semantic_candidates, settings)
from .io import canonical_json, file_hash, text_hash, write_json


def code_identity(mode):
    names = ['icd.py', 'icd_pipeline.py', 'chunk_batches.py', 'io.py']
    if mode != 'rules':
        names += ['chunk_models.py', 'chunk_parallel.py', 'semantic_compute.py', 'chunk_colab.py']
    return {name: file_hash(Path(__file__).with_name(name)) for name in names}


@contextmanager
def source_lock(path):
    lock = Path(path) / '.store.lock'
    # Portable bundles omit live lock files; reading them never creates one.
    if not lock.exists():
        yield
        return
    with lock.open('rb') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError('Input store has an active writer; stop it first') from exc
        yield


def source_manifest(input_dir):
    parent = read_json(Path(input_dir) / 'manifest.json')
    require(parent.get('state') == 'complete' and parent['identity']['operation'] == 'prepare-chunk-inputs',
            'Use the existing completed stage-5 document inputs; no QA catalog or fixed store is needed')
    require(text_hash(canonical_json(parent['identity'])) == parent['signature'], 'Input identity signature differs')
    return parent


def distinct(output, *sources):
    output = Path(output).resolve()
    for source in sources:
        path = Path(source).resolve()
        require(output != path and output not in path.parents and path not in output.parents, 'Output/backup overlaps an input or another output')


def methods(mode):
    require(mode in {'rules', 'semantic', 'combined'}, 'Unknown ICD mode')
    return ['rules', 'semantic', 'combined'] if mode == 'combined' else [mode]


class GPUScorer:
    """Reuse the pinned encoder and bounded length-bucketed/OOM-safe GPU scheduler."""
    def __init__(self, tokenizer_dir, taxonomy, *, batch_size=16, padded_tokens=8192):
        from .chunk_models import load_tokenizer
        from .semantic_compute import PackedCUDAEncoder
        import numpy as np
        self.tokenizer, self.tokenizer_signature = load_tokenizer(tokenizer_dir)
        self.encoder = PackedCUDAEncoder(self.tokenizer, max_batch_size=batch_size,
                                         max_padded_tokens=padded_tokens, tokenization_window=128)
        self.prototype_rows = prototypes(taxonomy)
        self.prototype_vectors = np.empty((len(self.prototype_rows), self.encoder.dimension), dtype='float32')
        self.encoder.encode_into((row[2] for row in self.prototype_rows), self.prototype_vectors)
        self.registry = self.encoder.registry

    def score(self, packets, config):
        import numpy as np
        segments = [bounded_segments(packet, self.tokenizer, config) for packet in packets]
        count = sum(map(len, segments))
        matrix = np.empty((count, self.encoder.dimension), dtype='float32')
        if count:
            self.encoder.encode_into((row['text'] for group in segments for row in group), matrix)
        offset, results = 0, []
        for group in segments:
            results.append(semantic_candidates(group, matrix[offset:offset + len(group)], self.prototype_rows, self.prototype_vectors))
            offset += len(group)
        return results


def build(input_dir, output_dir, config_path, *, mode='rules', tokenizer_dir=None,
          stop_after_batches=None, backup_dir=None, encoder_batch_size=16,
          padded_tokens=8192, documents_per_wave=16, scorer_factory=None):
    require(stop_after_batches is None or type(stop_after_batches) is int and stop_after_batches > 0, 'Positive stop-after-batches required')
    require(type(documents_per_wave) is int and 1 <= documents_per_wave <= 128, 'documents-per-wave must be 1..128')
    require(type(encoder_batch_size) is int and encoder_batch_size > 0 and type(padded_tokens) is int and padded_tokens >= 512,
            'Positive encoder batch size and padded-tokens >=512 required')
    method_names = methods(mode)
    input_dir, output_dir = Path(input_dir), Path(output_dir)
    distinct(output_dir, input_dir)
    config = settings(config_path)
    taxonomy = load_taxonomy(config['taxonomy_path'])
    parent = source_manifest(input_dir)
    model = None
    if mode != 'rules':
        require(tokenizer_dir is not None, 'Semantic/combined mode needs --tokenizer-dir and a CUDA GPU')
        from .chunk_models import MODEL_ID, MODEL_REVISION, load_tokenizer
        _, tokenizer_signature = load_tokenizer(tokenizer_dir)
        model = {'model_id': MODEL_ID, 'revision': MODEL_REVISION, 'tokenizer': tokenizer_signature,
                 'pooling': 'CLS + L2', 'dtype': 'float16'}
    identity = {'operation': 'icd-chapter-annotations', 'parent_signature': parent['signature'],
                'parent_manifest_sha256': file_hash(input_dir / 'manifest.json'), 'mode': mode,
                'config': config, 'taxonomy_sha256': file_hash(config['taxonomy_path']),
                'code': code_identity(mode), 'encoder': model}
    if backup_dir:
        distinct(backup_dir, input_dir, output_dir)
        if (Path(backup_dir) / 'manifest.json').is_file():
            require(read_json(Path(backup_dir) / 'manifest.json')['identity'] == identity,
                    'Backup input/code/mode/configuration differs; use its original settings or a new backup directory')
        restore_checkpoint(backup_dir, output_dir)
        if (output_dir / 'manifest.json').is_file() and (Path(backup_dir) / 'MODEL_REGISTRY.json').is_file() and not (output_dir / 'MODEL_REGISTRY.json').exists():
            write_json(output_dir / 'MODEL_REGISTRY.json', read_json(Path(backup_dir) / 'MODEL_REGISTRY.json'))
    rule_tagger, scorer = RuleTagger(taxonomy, config), None
    with source_lock(input_dir), output_store(output_dir, identity, resume=(output_dir / 'manifest.json').exists()) as (directory, manifest, committed):
        inputs = list(batches(input_dir, parent['signature']))
        require(len(inputs) == parent['batches'] and sum(i['documents'] for _, i in inputs) == parent['documents'], 'Input batch totals differ')
        require(len(committed) <= len(inputs), 'Too many output batches')
        for (_, previous), (_, source) in zip(committed, inputs):
            require(all(previous[k] == source[k] for k in ('index', 'documents', 'first_doc_id', 'last_doc_id')), 'Resumed output geometry differs')
        if manifest['state'] == 'complete':
            require(len(committed) == len(inputs), 'Complete manifest has missing batches')
            return manifest
        manifest.update(format='vibiomir-icd-document-batches-v1', taxonomy_version='WHO ICD-10 2019',
                        annotation_methods=method_names, calibration='pending', clinical_audit='deferred',
                        retrieval_boost_enabled=False, hard_filter_enabled=False)
        write_json(directory / 'manifest.json', manifest)
        added, started = 0, time.monotonic()
        for index in range(len(committed), len(inputs)):
            path, info = inputs[index]
            packets = list(read_rows(path))
            require(len(packets) == info['documents'], 'Input row count differs')
            if mode != 'rules' and scorer is None:
                scorer = (scorer_factory or GPUScorer)(tokenizer_dir, taxonomy, batch_size=encoder_batch_size, padded_tokens=padded_tokens)
                write_json(directory / 'MODEL_REGISTRY.json', scorer.registry)
                manifest.setdefault('execution_history', []).append({'first_batch': index, 'encoder_batch_size': encoder_batch_size,
                    'padded_tokens': padded_tokens, 'documents_per_wave': documents_per_wave, 'encoder': scorer.registry})
            rows = []
            for start in range(0, len(packets), documents_per_wave):
                wave = packets[start:start + documents_per_wave]
                semantic = scorer.score(wave, config) if scorer is not None else [[] for _ in wave]
                require(len(semantic) == len(wave), 'Scorer document count differs')
                rows.extend(annotate(packet, rule_tagger.tag(packet), scores, config, mode, manifest['signature'])
                            for packet, scores in zip(wave, semantic))
            committed.append(commit_batch(directory, manifest['signature'], index, rows))
            added += 1
            manifest = finish(directory, manifest, committed, False)
            manifest['annotation_records'] = manifest['documents'] * len(method_names)
            manifest['seconds_this_invocation'] = round(time.monotonic() - started, 3)
            write_json(directory / 'manifest.json', manifest)
            print(json.dumps({'phase': 'icd', 'mode': mode, 'batch': index, 'documents_done': manifest['documents'],
                              'unknown_in_batch': {m: sum(r['annotations'][m]['unknown'] for r in rows) for m in method_names}}), flush=True)
            if backup_dir:
                save_checkpoint(directory, backup_dir, committed[-1][0])
                if (directory / 'MODEL_REGISTRY.json').exists():
                    # Registry is small; preserve it alongside each checkpoint.
                    write_json(Path(backup_dir) / 'MODEL_REGISTRY.json', read_json(directory / 'MODEL_REGISTRY.json'))
            if stop_after_batches and added >= stop_after_batches and len(committed) < len(inputs):
                return manifest
        manifest = finish(directory, manifest, committed, True)
        manifest['annotation_records'] = manifest['documents'] * len(method_names)
        write_json(directory / 'manifest.json', manifest)
        if backup_dir:
            save_checkpoint(directory, backup_dir)
        return manifest


def check_evidence(item, packet):
    options = [field for field in fields(packet) if field['field'] == item['field'] and field.get('region_id') == item['region_id']]
    require(len(options) == 1, 'Evidence field/region missing')
    field = options[0]
    require(type(item['start']) is int and type(item['end']) is int and 0 <= item['start'] < item['end'] <= len(field['text']), 'Evidence offset invalid')
    expected = evidence(field, item['start'], item['end'])
    require(all(item.get(k) == v for k, v in expected.items()), 'Evidence/source slice differs')


def validate(input_dir, output_dir, config_path):
    config = settings(config_path)
    taxonomy = load_taxonomy(config['taxonomy_path'])
    parent = source_manifest(input_dir)
    root = Path(output_dir)
    require((root / 'manifest.json').is_file(), 'No S06 output manifest; run the ICD build first, using this output directory')
    from .io import writer_lock
    with source_lock(input_dir), writer_lock(root):
        manifest = read_json(root / 'manifest.json')
        identity = manifest['identity']
        require(manifest['state'] == 'complete', 'Resume ICD build before validation')
        require(text_hash(canonical_json(identity)) == manifest['signature'], 'Output identity differs')
        require(identity['parent_signature'] == parent['signature'] and identity['parent_manifest_sha256'] == file_hash(Path(input_dir) / 'manifest.json'), 'Wrong input snapshot')
        require(identity['config'] == config and identity['taxonomy_sha256'] == file_hash(config['taxonomy_path']) and
                identity['code'] == code_identity(identity['mode']), 'ICD config/taxonomy/code changed; use the original revision or rebuild separately')
        names, counts, docs, parts = methods(identity['mode']), {}, 0, 0
        tagger = RuleTagger(taxonomy, config)
        if identity['mode'] != 'rules':
            registry = read_json(root / 'MODEL_REGISTRY.json')
            require(0 < registry['parameters'] <= 15_000_000_000 and registry['model_id'] == identity['encoder']['model_id'] and
                    registry['revision'] == identity['encoder']['revision'], 'Model registry mismatch')
        counts = {name: Counter() for name in names}
        for pair in zip_longest(batches(input_dir, parent['signature']), batches(root, manifest['signature'])):
            require(all(p is not None for p in pair), 'Batch coverage differs')
            (left, li), (right, ri) = pair
            require(all(li[k] == ri[k] for k in ('index', 'documents', 'first_doc_id', 'last_doc_id')), 'Batch geometry differs')
            count = 0
            for packet, row in zip_longest(read_rows(left), read_rows(right)):
                require(packet is not None and row is not None, 'Document coverage differs')
                require(all(row[k] == packet[k] for k in ('doc_id', 'derived_version_id', 'parent_version_id', 'availability')), 'Source version/ID differs')
                require(set(row['annotations']) == set(names), 'Annotation method coverage differs')
                rules = tagger.tag(packet)
                semantic = row['annotations'].get('semantic', {}).get('candidates', [])
                if identity['mode'] != 'rules':
                    require(not semantic or len(semantic) == 22 and {c['chapter'] for c in semantic} == set(CHAPTERS),
                            'Semantic scores must cover all 22 chapters when present')
                    require(semantic == sorted(semantic, key=lambda c: (-c['score'], CHAPTERS.index(c['chapter']))), 'Semantic score ordering differs')
                # Stored semantic evidence/scores are checked, not re-encoded on CPU.
                # All semantic scores persist; only leading/selected scores retain text.
                for annotation in row['annotations'].values():
                    require(annotation['confidence'] is None and annotation['calibration'] == 'pending' and
                            annotation['human_reviewed'] is False and annotation['retrieval_boost_enabled'] is False and
                            annotation['hard_filter_enabled'] is False, 'Unreviewed metadata was promoted to approved/active state')
                    require(len(set(annotation['labels'])) == len(annotation['labels']) and set(annotation['labels']) <= set(CHAPTERS), 'Invalid/duplicate chapter label')
                    for candidate in annotation['candidates']:
                        require(candidate['chapter'] in CHAPTERS and type(candidate['score']) in (float, int) and
                                -1 <= candidate['score'] <= 1, 'Invalid chapter score')
                        for item in candidate['evidence']:
                            check_evidence(item, packet)
                for item in row['rule_scan']['suppressed_matches']:
                    check_evidence(item, packet)
                expected = annotate(packet, rules, semantic, config, identity['mode'], manifest['signature'])
                if identity['mode'] == 'rules':
                    require(row == expected, 'Rule annotations differ from deterministic recomputation')
                else:
                    require(row['rule_scan'] == expected['rule_scan'], 'Rule scan metadata differs')
                    for name in names:
                        for key in ('annotation_id', 'labels', 'unknown', 'reason'):
                            require(row['annotations'][name][key] == expected['annotations'][name][key], 'Selection policy differs from stored scores')
                        for chapter in row['annotations'][name]['labels']:
                            require(any(c['chapter'] == chapter and c['evidence'] for c in row['annotations'][name]['candidates']), 'Selected label lacks evidence')
                for name in names:
                    counts[name]['unknown'] += row['annotations'][name]['unknown']
                    counts[name].update(row['annotations'][name]['labels'])
                docs += 1
                count += 1
            require(count == li['documents'], 'Batch document count differs')
            parts += 1
            if parts % 25 == 0:
                print(json.dumps({'phase': 'validate_icd', 'batches': parts, 'documents': docs}), flush=True)
        require(docs == manifest['documents'] == parent['documents'] and parts == manifest['batches'] == parent['batches'] and
                manifest['annotation_records'] == docs * len(names), 'Global accounting differs')
        report = {'validation': 'passed', 'documents': docs, 'batches': parts, 'methods': {k: dict(v) for k, v in counts.items()},
                  'annotation_records': docs * len(names), 'manifest_sha256': file_hash(root / 'manifest.json'),
                  'source_evidence_checked': True, 'semantic_vectors_recomputed': False,
                  'clinical_quality_measured': False, 'calibration': 'pending', 'human_qa': 'pending',
                  'ready_for_retrieval': False, 'retrieval_boost_enabled': False}
        write_json(root / 'VALIDATION.json', report)
        return report


def tag_text(text_file, output_file, config_path, *, entity_kind, entity_id, mode='rules', tokenizer_dir=None):
    """Same chapter schema for future query/chunk integration, bound to supplied text."""
    require(entity_kind in {'query', 'chunk', 'document'} and entity_id, 'Declare entity kind and stable ID')
    methods(mode)
    output_file = Path(output_file)
    require(not output_file.exists(), 'Text annotation output exists; choose another file')
    config = settings(config_path)
    taxonomy = load_taxonomy(config['taxonomy_path'])
    text = Path(text_file).read_text(encoding='utf-8')
    packet = {'doc_id': entity_id, 'derived_version_id': file_hash(text_file), 'parent_version_id': None,
              'availability': 'provided_text', 'regions': [{'region_id': 'provided', 'text': text,
              'source_start': 0, 'source_kind': 'provided_text', 'source_locator_json': canonical_json({'file_sha256': file_hash(text_file)})}]}
    source = {'entity_kind': entity_kind, 'entity_id': entity_id, 'text_file_sha256': file_hash(text_file),
              'taxonomy_sha256': file_hash(config['taxonomy_path']), 'config': config, 'code': code_identity(mode)}
    semantic = []
    registry = None
    if mode != 'rules':
        require(tokenizer_dir is not None, 'Semantic text tagging needs a pinned tokenizer and CUDA GPU')
        scorer = GPUScorer(tokenizer_dir, taxonomy)
        semantic, registry = scorer.score([packet], config)[0], scorer.registry
        source['encoder'] = registry
    result = annotate(packet, RuleTagger(taxonomy, config).tag(packet), semantic, config, mode, text_hash(canonical_json(source)))
    result.pop('doc_id')
    result.update(source=source, scope=f'{entity_kind}_topic_not_patient_diagnosis', entity_kind=entity_kind, entity_id=entity_id)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    write_json(output_file, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    run = commands.add_parser('build')
    run.add_argument('--mode', choices=['rules', 'semantic', 'combined'], default='rules')
    run.add_argument('--tokenizer-dir', type=Path)
    run.add_argument('--stop-after-batches', type=int)
    run.add_argument('--backup-dir', type=Path)
    run.add_argument('--encoder-batch-size', type=int, default=16)
    run.add_argument('--padded-tokens', type=int, default=8192)
    run.add_argument('--documents-per-wave', type=int, default=16)
    commands.add_parser('validate')
    for command in commands.choices.values():
        command.add_argument('--input-dir', type=Path, default=Path('artifacts/chunk_inputs_stage5_full'))
        command.add_argument('--output-dir', type=Path)
        command.add_argument('--config', type=Path, default=Path('configs/icd_stage6.json'))
    text = commands.add_parser('tag-text')
    text.add_argument('--text-file', required=True, type=Path)
    text.add_argument('--output-file', required=True, type=Path)
    text.add_argument('--entity-kind', required=True, choices=['query', 'chunk', 'document'])
    text.add_argument('--entity-id', required=True)
    text.add_argument('--mode', choices=['rules', 'semantic', 'combined'], default='rules')
    text.add_argument('--tokenizer-dir', type=Path)
    text.add_argument('--config', type=Path, default=Path('configs/icd_stage6.json'))
    args = parser.parse_args()
    try:
        if args.command != 'tag-text' and args.output_dir is None:
            args.output_dir = Path('artifacts/icd_stage6_' + (args.mode if args.command == 'build' else 'rules'))
        if args.command == 'build':
            result = build(args.input_dir, args.output_dir, args.config, mode=args.mode, tokenizer_dir=args.tokenizer_dir,
                           stop_after_batches=args.stop_after_batches, backup_dir=args.backup_dir,
                           encoder_batch_size=args.encoder_batch_size, padded_tokens=args.padded_tokens,
                           documents_per_wave=args.documents_per_wave)
        elif args.command == 'validate':
            result = validate(args.input_dir, args.output_dir, args.config)
        else:
            result = tag_text(args.text_file, args.output_file, args.config, entity_kind=args.entity_kind,
                              entity_id=args.entity_id, mode=args.mode, tokenizer_dir=args.tokenizer_dir)
        print(json.dumps({k: v for k, v in result.items() if k != 'identity'}, ensure_ascii=False, indent=2))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    main()
