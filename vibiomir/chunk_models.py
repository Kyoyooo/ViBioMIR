"""Pinned public tokenizer; CUDA sentence encoder loaded only for semantic jobs."""
import json
from pathlib import Path

from .io import canonical_json, file_hash, text_hash, write_json

MODEL_ID = "BAAI/bge-m3"
MODEL_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
MODEL_COMMIT_DATE = "2024-07-03"


def download_tokenizer(directory):
    from transformers import AutoTokenizer
    directory = Path(directory)
    if directory.exists() and any(directory.iterdir()):
        raise ValueError("Choose a new empty tokenizer directory")
    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, revision=MODEL_REVISION,
                                             use_fast=True, trust_remote_code=False)
    directory.mkdir(parents=True, exist_ok=True)
    tokenizer.save_pretrained(directory)
    files = {p.name: file_hash(p) for p in sorted(directory.iterdir()) if p.is_file()}
    registry = {"model_id": MODEL_ID, "revision": MODEL_REVISION,
                "commit_date": MODEL_COMMIT_DATE, "files": files}
    write_json(directory / "REGISTRY.json", registry)
    return registry


def load_tokenizer(directory):
    from transformers import AutoTokenizer
    directory = Path(directory)
    registry = json.loads((directory / "REGISTRY.json").read_text(encoding="utf-8"))
    if (registry["model_id"], registry["revision"]) != (MODEL_ID, MODEL_REVISION):
        raise ValueError("Tokenizer registry does not match the pinned BGE-M3 revision")
    for name, digest in registry["files"].items():
        if Path(name).name != name or file_hash(directory / name) != digest:
            raise ValueError(f"Tokenizer checksum mismatch: {name}")
    tokenizer = AutoTokenizer.from_pretrained(directory, use_fast=True, local_files_only=True,
                                             trust_remote_code=False)
    if not tokenizer.is_fast:
        raise ValueError("A fast tokenizer with original character offsets is required")
    # Avoid irrelevant 8,192-token warnings on whole source documents; each
    # emitted retrieval_text is separately checked against the configured cap.
    tokenizer.model_max_length = 10**12
    return tokenizer, text_hash(canonical_json(registry))


class SentenceEncoder:
    def __init__(self, tokenizer, batch_size=16):
        import torch
        from transformers import AutoModel
        if not torch.cuda.is_available():
            raise ValueError("Semantic runner requires CUDA. Select a GPU runtime in Colab; see the runbook.")
        if batch_size < 1:
            raise ValueError("encoder_batch_size must be positive")
        self.torch, self.tokenizer, self.batch_size = torch, tokenizer, batch_size
        self.model = AutoModel.from_pretrained(MODEL_ID, revision=MODEL_REVISION,
                    trust_remote_code=False, torch_dtype=torch.float16).eval().to("cuda")
        parameters = sum(p.numel() for p in self.model.parameters())
        if parameters > 15_000_000_000:
            raise ValueError("Model exceeds the competition's 15B limit")
        self.registry = {"model_id": MODEL_ID, "revision": MODEL_REVISION, "commit_date": MODEL_COMMIT_DATE,
                         "parameters": parameters, "pooling": "CLS + L2", "dtype": "float16",
                         "device": torch.cuda.get_device_name(0), "torch": torch.__version__}

    def __call__(self, texts):
        vectors = []
        for start in range(0, len(texts), self.batch_size):
            batch = self.tokenizer(texts[start:start + self.batch_size], padding=True,
                                   truncation=False, return_tensors="pt")
            if batch["input_ids"].shape[1] > 512:
                raise ValueError("Semantic sentence exceeds 512 tokens; source splitting must run first")
            batch = {key: value.to("cuda") for key, value in batch.items()}
            with self.torch.inference_mode():
                output = self.model(**batch).last_hidden_state[:, 0]
                output = self.torch.nn.functional.normalize(output.float(), p=2, dim=1)
                vectors.extend(output.cpu().tolist())
        return vectors
