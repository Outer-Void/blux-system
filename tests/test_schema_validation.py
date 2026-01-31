from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from blux_system.core import build_replay_report, make_receipt, make_snapshot

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


def test_snapshot_schema_validation_with_profile() -> None:
    snapshot = make_snapshot(
        inputs=[{"path": "inputs/data.txt", "hash": "sha256:abc", "size": 12}],
        outputs=[{"path": "outputs/result.json", "hash": "sha256:def", "size": 34}],
        created_at="2024-01-01T00:00:00Z",
        profile_id="profile-basic",
        profile_version="2024.04",
        device="cpu",
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


def test_receipt_schema_validation_with_profile() -> None:
    snapshot = make_snapshot(
        inputs=[{"path": "inputs/data.txt", "hash": "sha256:abc", "size": 12}],
        outputs=[{"path": "outputs/result.json", "hash": "sha256:def", "size": 34}],
        created_at="2024-01-01T00:00:00Z",
    )
    receipt = make_receipt(
        snapshot,
        created_at="2024-01-01T00:00:00Z",
        agent_headers={
            "input_hash": "sha256:input",
            "model_version": "model-x",
            "contract_version": "1.0",
            "requested_model_version": "model-x",
            "resolved_model_version": "model-x",
            "schema_version": "1.0",
            "profile_id": "profile-pro",
            "profile_version": "2024.04",
            "device": "gpu",
        },
    )
    schema = load_schema("execution_receipt.schema.json")
    jsonschema.validate(receipt, schema)


def test_replay_report_schema_validation(tmp_path: Path) -> None:
    input_dir = tmp_path / "inputs"
    output_dir = tmp_path / "outputs"
    input_dir.mkdir()
    output_dir.mkdir()

    (input_dir / "alpha.txt").write_text("alpha", encoding="utf-8")
    (output_dir / "result.json").write_text("{\"ok\":true}", encoding="utf-8")

    snapshot = make_snapshot(
        inputs=[{"path": "inputs/alpha.txt", "hash": "sha256:aaa", "size": 5}],
        outputs=[{"path": "outputs/result.json", "hash": "sha256:bbb", "size": 11}],
        created_at="2024-01-01T00:00:00Z",
    )
    receipt = make_receipt(snapshot, created_at="2024-01-01T00:00:00Z")
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    report = build_replay_report(receipt_path, tmp_path)
    schema = load_schema("replay_report.schema.json")
    jsonschema.validate(report, schema)
