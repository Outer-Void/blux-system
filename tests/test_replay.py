from __future__ import annotations

import json
from pathlib import Path

from blux_system.core import (
    build_replay_report,
    build_snapshot_from_dirs,
    canonical_json_bytes,
    make_receipt,
)


def test_replay_report_determinism(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("BLUX_DETERMINISTIC_TIMESTAMP", "2024-01-01T00:00:00Z")
    monkeypatch.setenv("BLUX_AGENT_INPUT_HASH", "sha256:agent")
    monkeypatch.setenv("BLUX_AGENT_MODEL_VERSION", "gpt-test")
    monkeypatch.setenv("BLUX_AGENT_CONTRACT_VERSION", "0.1")

    input_dir = tmp_path / "inputs"
    output_dir = tmp_path / "outputs"
    input_dir.mkdir()
    output_dir.mkdir()

    (input_dir / "alpha.txt").write_text("alpha", encoding="utf-8")
    (output_dir / "result.json").write_text("{\"ok\":true}", encoding="utf-8")

    snapshot = build_snapshot_from_dirs(input_dir, output_dir)
    receipt = make_receipt(snapshot)
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    report_a = build_replay_report(receipt_path, output_dir)
    report_b = build_replay_report(receipt_path, output_dir)

    assert canonical_json_bytes(report_a) == canonical_json_bytes(report_b)


def test_replay_detects_hash_mismatch(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("BLUX_DETERMINISTIC_TIMESTAMP", "2024-01-01T00:00:00Z")

    input_dir = tmp_path / "inputs"
    output_dir = tmp_path / "outputs"
    input_dir.mkdir()
    output_dir.mkdir()

    (input_dir / "alpha.txt").write_text("alpha", encoding="utf-8")
    output_file = output_dir / "result.json"
    output_file.write_text("{\"ok\":true}", encoding="utf-8")

    snapshot = build_snapshot_from_dirs(input_dir, output_dir)
    receipt = make_receipt(snapshot)
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    output_file.write_text("{\"ok\":false}", encoding="utf-8")

    report = build_replay_report(receipt_path, output_dir)

    assert report["summary"]["ok"] is False
    assert report["summary"]["hash_mismatches"] == 1


def test_replay_dataset_fixture_verification(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("BLUX_DETERMINISTIC_TIMESTAMP", "2024-01-01T00:00:00Z")

    input_dir = tmp_path / "inputs"
    output_dir = tmp_path / "outputs"
    fixtures_dir = tmp_path / "fixtures"
    input_dir.mkdir()
    output_dir.mkdir()
    fixtures_dir.mkdir()

    (input_dir / "alpha.txt").write_text("alpha", encoding="utf-8")
    (output_dir / "result.json").write_text("{\"ok\":true}", encoding="utf-8")
    fixture_path = fixtures_dir / "fixture-1.json"
    fixture_path.write_text("{\"fixture\":1}", encoding="utf-8")

    snapshot = build_snapshot_from_dirs(input_dir, output_dir)
    receipt = make_receipt(
        snapshot,
        dataset_fixture={
            "id": "fixture-1",
            "hash": "sha256:invalid",
            "path": "fixtures/fixture-1.json",
        },
    )
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    report = build_replay_report(receipt_path, tmp_path)

    assert report["summary"]["ok"] is False
    assert report["summary"]["fixture_hash_mismatches"] == 1
