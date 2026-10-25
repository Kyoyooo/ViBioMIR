"""Small deterministic IO primitives shared by preparation commands."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
from pathlib import Path


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def text_hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def file_hash(path):
    hasher = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def write_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


@contextmanager
def writer_lock(directory):
    """Protect a local store from concurrent builders/checkpoint creation."""
    with (Path(directory) / ".store.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("Another process is using this store") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
