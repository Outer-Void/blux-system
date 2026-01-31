from __future__ import annotations

import json
from pathlib import Path

from blux_system.core import build_receipt_from_snapshot, build_snapshot_from_dirs, canonical_json_bytes


def test_snapshot_determinism(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("BLUX_DETERMINISTIC_TIMESTAMP", "2024-01-01T00:00:00Z")

    input_dir = tmp_path / "inputs"
    output_dir = tmp_path / "outputs"
    input_dir.mkdir()
    output_dir.mkdir()

    (input_dir / "alpha.txt").write_text("alpha", encoding="utf-8")
    (input_dir / "beta.txt").write_text("beta", encoding="utf-8")
    (output_dir / "result.json").write_text("{\"ok\":true}", encoding="utf-8")

    snapshot_a = build_snapshot_from_dirs(input_dir, output_dir)
    snapshot_b = build_snapshot_from_dirs(input_dir, output_dir)

    assert canonical_json_bytes(snapshot_a) == canonical_json_bytes(snapshot_b)


def test_receipt_determinism(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("BLUX_DETERMINISTIC_TIMESTAMP", "2024-01-01T00:00:00Z")

    input_dir = tmp_path / "inputs"
    output_dir = tmp_path / "outputs"
    input_dir.mkdir()
    output_dir.mkdir()

    (input_dir / "alpha.txt").write_text("alpha", encoding="utf-8")
    (output_dir / "result.json").write_text("{\"ok\":true}", encoding="utf-8")

    snapshot = build_snapshot_from_dirs(input_dir, output_dir)
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_bytes(canonical_json_bytes(snapshot))

    receipt_a = build_receipt_from_snapshot(snapshot_path)
    receipt_b = build_receipt_from_snapshot(snapshot_path)

    assert canonical_json_bytes(receipt_a) == canonical_json_bytes(receipt_b)
