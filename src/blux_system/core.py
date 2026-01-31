from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

CONTRACT_VERSION = "0.1"


@dataclass(frozen=True)
class FileRecord:
    path: str
    hash: str
    size: int

    def as_dict(self) -> dict[str, object]:
        return {"path": self.path, "hash": self.hash, "size": self.size}


def canonical_json_bytes(data: object) -> bytes:
    return json.dumps(
        data,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _hash_bytes(data: bytes) -> str:
    digest = hashlib.sha256(data).hexdigest()
    return f"sha256:{digest}"


def _hash_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8192), b""):
            hasher.update(chunk)
    return f"sha256:{hasher.hexdigest()}"


def _deterministic_timestamp() -> str:
    override = os.getenv("BLUX_DETERMINISTIC_TIMESTAMP")
    if override:
        return override
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _sorted_file_records(paths: Iterable[Path], base: Path) -> list[FileRecord]:
    records = []
    for path in paths:
        relative = path.relative_to(base).as_posix()
        records.append(
            FileRecord(
                path=relative,
                hash=_hash_file(path),
                size=path.stat().st_size,
            )
        )
    return sorted(records, key=lambda record: record.path)


def _collect_files(root: Path) -> list[FileRecord]:
    root = root.resolve()
    if root.is_file():
        return _sorted_file_records([root], root.parent)
    paths = sorted(p for p in root.rglob("*") if p.is_file())
    return _sorted_file_records(paths, root)


def make_snapshot(
    inputs: Sequence[FileRecord] | Sequence[dict[str, object]],
    outputs: Sequence[FileRecord] | Sequence[dict[str, object]],
    *,
    created_at: str | None = None,
    contract_version: str = CONTRACT_VERSION,
) -> dict[str, object]:
    created = created_at or _deterministic_timestamp()

    def normalize(records: Sequence[FileRecord] | Sequence[dict[str, object]]) -> list[dict[str, object]]:
        normalized = []
        for record in records:
            if isinstance(record, FileRecord):
                normalized.append(record.as_dict())
            else:
                normalized.append(
                    {
                        "path": record["path"],
                        "hash": record["hash"],
                        "size": record.get("size", 0),
                    }
                )
        return sorted(normalized, key=lambda item: item["path"])

    payload = {
        "contract_version": contract_version,
        "created_at": created,
        "inputs": normalize(inputs),
        "outputs": normalize(outputs),
    }
    snapshot_hash = _hash_bytes(canonical_json_bytes(payload))
    payload["snapshot_hash"] = snapshot_hash
    return payload


def make_receipt(
    snapshot: dict[str, object],
    *,
    created_at: str | None = None,
    contract_version: str = CONTRACT_VERSION,
) -> dict[str, object]:
    created = created_at or _deterministic_timestamp()
    snapshot_hash = snapshot.get("snapshot_hash")
    if not snapshot_hash:
        snapshot_hash = _hash_bytes(
            canonical_json_bytes({k: v for k, v in snapshot.items() if k != "snapshot_hash"})
        )

    payload = {
        "contract_version": contract_version,
        "created_at": created,
        "snapshot": {
            "hash": snapshot_hash,
            "contract_version": snapshot.get("contract_version"),
        },
        "snapshot_hash": snapshot_hash,
    }
    receipt_hash = _hash_bytes(canonical_json_bytes(payload))
    payload["receipt_hash"] = receipt_hash
    return payload


def load_state(path: str | Path) -> dict[str, object]:
    data = Path(path).read_text(encoding="utf-8")
    return json.loads(data)


def save_state(path: str | Path, state: dict[str, object]) -> None:
    content = canonical_json_bytes(state)
    Path(path).write_bytes(content)


def build_snapshot_from_dirs(input_dir: Path, output_dir: Path) -> dict[str, object]:
    inputs = _collect_files(input_dir)
    outputs = _collect_files(output_dir)
    return make_snapshot(inputs, outputs)


def build_receipt_from_snapshot(snapshot_path: Path) -> dict[str, object]:
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    return make_receipt(snapshot)
