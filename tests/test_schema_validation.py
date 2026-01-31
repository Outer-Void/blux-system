from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from blux_system.core import make_receipt, make_snapshot

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "schemas"


def load_schema(name: str) -> dict:
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


def test_snapshot_schema_validation() -> None:
    snapshot = make_snapshot(
        inputs=[{"path": "inputs/data.txt", "hash": "sha256:abc", "size": 12}],
        outputs=[{"path": "outputs/result.json", "hash": "sha256:def", "size": 34}],
        created_at="2024-01-01T00:00:00Z",
    )
    schema = load_schema("snapshot.schema.json")
    jsonschema.validate(snapshot, schema)


def test_receipt_schema_validation() -> None:
    snapshot = make_snapshot(
        inputs=[{"path": "inputs/data.txt", "hash": "sha256:abc", "size": 12}],
        outputs=[{"path": "outputs/result.json", "hash": "sha256:def", "size": 34}],
        created_at="2024-01-01T00:00:00Z",
    )
    receipt = make_receipt(snapshot, created_at="2024-01-01T00:00:00Z")
    schema = load_schema("execution_receipt.schema.json")
    jsonschema.validate(receipt, schema)
