"""Local filesystem artifact storage: checksums and atomic JSON/JSONL/gzip writes."""
import hashlib
import json
import os
from pathlib import Path


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, path)


def write_jsonl(path, rows):
    path.write_text(''.join(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n' for row in rows), encoding='utf-8')


def write_gzip(target, raw):
    import gzip
    temporary = target.with_suffix(target.suffix + '.tmp')
    temporary.write_bytes(gzip.compress(raw, compresslevel=5, mtime=0))
    temporary.replace(target)


class LocalArtifactStorage:
    def sha256(self, path):
        return sha256(path)

    def write_json(self, path, value):
        write_json(path, value)

    def write_jsonl(self, path, rows):
        write_jsonl(path, rows)

    def write_gzip(self, target, raw):
        write_gzip(target, raw)
