"""Manually create a full Colab handoff; optionally retain all finished CPU batches."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vibiomir.chunk_full_bundle import make_full_bundle


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--tokenizer-dir", type=Path, required=True)
    parser.add_argument("--checkpoint-dir", type=Path)
    parser.add_argument("--config", type=Path, default=ROOT / "configs/chunking_stage5.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = make_full_bundle(args.input_dir, args.tokenizer_dir, args.output,
                                 checkpoint_dir=args.checkpoint_dir, config_path=args.config)
    except (ValueError, OSError, ImportError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
