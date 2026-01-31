from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from blux_system.core import build_replay_report

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "schemas"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def load_schema(name: str) -> dict:
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


def test_legacy_receipt_schema_compatibility(tmp_path: Path) -> None:
    receipt_path = FIXTURES_DIR / "receipt_v0_1.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    schema = load_schema("execution_receipt.schema.json")
    jsonschema.validate(receipt, schema)

    outputs_dir = tmp_path / "outputs"
    outputs_dir.mkdir()
    (outputs_dir / "result.json").write_text("{\"legacy\":true}", encoding="utf-8")
    report = build_replay_report(receipt_path, tmp_path)
    assert report["schema_valid"] is True
