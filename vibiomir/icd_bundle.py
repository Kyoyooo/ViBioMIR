"""Package existing prepared documents for the S06 CUDA metadata run; no model weights."""
import argparse
import json
from pathlib import Path
import zipfile

from .chunk_batches import batches
from .icd import load_taxonomy, require, settings
from .icd_pipeline import distinct, source_lock, source_manifest


def bundle(input_dir, tokenizer_dir, output, config_path, *, project_root=None):
    from .chunk_models import load_tokenizer
    root = Path(project_root) if project_root else Path(__file__).resolve().parents[1]
    input_dir, tokenizer_dir, output = Path(input_dir), Path(tokenizer_dir), Path(output)
    distinct(output, input_dir, tokenizer_dir)
    require(not output.exists(), 'Bundle already exists; use a new ZIP name')
    config = settings(config_path)
    load_taxonomy(config['taxonomy_path'])
    load_tokenizer(tokenizer_dir)
    parent = source_manifest(input_dir)
    with source_lock(input_dir):
        parts = list(batches(input_dir, parent['signature']))
        require(len(parts) == parent['batches'] and sum(i['documents'] for _, i in parts) == parent['documents'], 'Input accounting differs')
        entries = [(p, p.relative_to(root).as_posix()) for p in sorted((root / 'vibiomir').glob('*.py'))]
        entries += [(p, p.relative_to(root).as_posix()) for p in sorted((root / 'tests').glob('test_stage6*.py'))]
        for name in ('requirements-stage3.txt', 'requirements-stage4.txt', 'requirements-stage5.txt',
                     'requirements-stage5-colab.txt', 'requirements-stage5-semantic.txt',
                     'requirements-stage6.txt', 'requirements-stage6-colab.txt', 'docs/ICD_STAGE6.md',
                     'colab/stage6_icd.ipynb'):
            entries.append((root / name, name))
        require(config['taxonomy_path'] == 'taxonomy/icd10_chapters.json', 'Portable bundle expects taxonomy/icd10_chapters.json; keep the canonical path')
        entries.extend([(Path(config_path), 'configs/icd_stage6.json'), (Path(config['taxonomy_path']), config['taxonomy_path']),
                        (input_dir / 'manifest.json', 'inputs/manifest.json')])
        for path, _ in parts:
            for name in ('batch.json', 'documents.jsonl.gz'):
                entries.append((path / name, f'inputs/{path.name}/{name}'))
        entries += [(p, f'tokenizer/{p.name}') for p in sorted(tokenizer_dir.iterdir()) if p.is_file()]
        require(all(p.is_file() for p, _ in entries), 'A required bundle source file is missing')
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(output.suffix + '.partial')
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
            for path, name in entries:
                archive.write(path, name)
        temporary.replace(output)
    return {'output': str(output), 'documents': parent['documents'], 'batches': parent['batches'],
            'weights_included': False, 'source_databases_included': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir', type=Path, default=Path('artifacts/chunk_inputs_stage5_full'))
    parser.add_argument('--tokenizer-dir', type=Path, default=Path('artifacts/tokenizers/bge_m3_stage5'))
    parser.add_argument('--config', type=Path, default=Path('configs/icd_stage6.json'))
    parser.add_argument('--output', type=Path, default=Path('artifacts/stage6_icd_colab.zip'))
    args = parser.parse_args()
    try:
        print(json.dumps(bundle(args.input_dir, args.tokenizer_dir, args.output, args.config), indent=2))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    main()
