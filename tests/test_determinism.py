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


def test_bundle_determinism(monkeypatch) -> None:
    monkeypatch.setenv("BLUX_DETERMINISTIC_TIMESTAMP", "2024-01-01T00:00:00Z")

    from blux_system.core import make_snapshot, make_receipt

    snapshot_a = make_snapshot(
        inputs=[{"path": "inputs/a.txt", "hash": "sha256:aaa", "size": 1}],
        outputs=[{"path": "outputs/z.txt", "hash": "sha256:zzz", "size": 2}],
        output_bundles=[
            {
                "bundle_id": "bundle-b",
                "files": [
                    {"path": "outputs/b.txt", "hash": "sha256:bbb", "size": 3},
                    {"path": "outputs/a.txt", "hash": "sha256:aaa", "size": 4},
                ],
            },
            {
                "bundle_id": "bundle-a",
                "files": [
                    {"path": "outputs/c.txt", "hash": "sha256:ccc", "size": 5},
                ],
            },
        ],
        patch_bundles=[
            {
                "bundle_id": "patch-b",
                "base_path": "inputs/a.txt",
                "patches": [{"path": "outputs/a.patch", "hash": "sha256:ppp", "size": 6}],
                "outputs": [{"path": "outputs/a.out", "hash": "sha256:ooo", "size": 7}],
            }
        ],
    )
    snapshot_b = make_snapshot(
        inputs=[{"path": "inputs/a.txt", "hash": "sha256:aaa", "size": 1}],
        outputs=[{"path": "outputs/z.txt", "hash": "sha256:zzz", "size": 2}],
        output_bundles=[
            {
                "bundle_id": "bundle-a",
                "files": [
                    {"path": "outputs/c.txt", "hash": "sha256:ccc", "size": 5},
                ],
            },
            {
                "bundle_id": "bundle-b",
                "files": [
                    {"path": "outputs/a.txt", "hash": "sha256:aaa", "size": 4},
                    {"path": "outputs/b.txt", "hash": "sha256:bbb", "size": 3},
                ],
            },
        ],
        patch_bundles=[
            {
                "bundle_id": "patch-b",
                "base_path": "inputs/a.txt",
                "patches": [{"path": "outputs/a.patch", "hash": "sha256:ppp", "size": 6}],
                "outputs": [{"path": "outputs/a.out", "hash": "sha256:ooo", "size": 7}],
            }
        ],
    )

    assert canonical_json_bytes(snapshot_a) == canonical_json_bytes(snapshot_b)
    assert canonical_json_bytes(make_receipt(snapshot_a)) == canonical_json_bytes(make_receipt(snapshot_b))


def test_receipt_run_graph_determinism(monkeypatch) -> None:
    monkeypatch.setenv("BLUX_DETERMINISTIC_TIMESTAMP", "2024-01-01T00:00:00Z")

    from blux_system.core import make_receipt, make_snapshot

    snapshot = make_snapshot(
        inputs=[{"path": "inputs/a.txt", "hash": "sha256:aaa", "size": 1}],
        outputs=[{"path": "outputs/z.txt", "hash": "sha256:zzz", "size": 2}],
    )

    receipt_a = make_receipt(
        snapshot,
        policy_pack={"id": "policy-pack", "version": "1.2.3"},
        reasoning_pack={"id": "reason-pack", "version": "4.5.6"},
        run_steps=[
            {
                "id": "step-b",
                "agent": "agent-b",
                "input_ref": "inputs/a.txt",
                "output_ref": "outputs/z.txt",
                "status": "ok",
            },
            {
                "id": "step-a",
                "agent": "agent-a",
                "input_ref": "inputs/a.txt",
                "output_ref": "outputs/z.txt",
                "status": "ok",
            },
        ],
    )
    receipt_b = make_receipt(
        snapshot,
        policy_pack={"version": "1.2.3", "id": "policy-pack"},
        reasoning_pack={"version": "4.5.6", "id": "reason-pack"},
        run_steps=[
            {
                "id": "step-a",
                "agent": "agent-a",
                "input_ref": "inputs/a.txt",
                "output_ref": "outputs/z.txt",
                "status": "ok",
            },
            {
                "id": "step-b",
                "agent": "agent-b",
                "input_ref": "inputs/a.txt",
                "output_ref": "outputs/z.txt",
                "status": "ok",
            },
        ],
    )

    assert canonical_json_bytes(receipt_a) == canonical_json_bytes(receipt_b)


def test_profile_fields_determinism(monkeypatch) -> None:
    monkeypatch.setenv("BLUX_DETERMINISTIC_TIMESTAMP", "2024-01-01T00:00:00Z")

    from blux_system.core import make_receipt, make_snapshot

    snapshot_a = make_snapshot(
        inputs=[{"path": "inputs/a.txt", "hash": "sha256:aaa", "size": 1}],
        outputs=[{"path": "outputs/z.txt", "hash": "sha256:zzz", "size": 2}],
        profile_id="profile-1",
        profile_version="2024.04",
        device="cpu",
    )
    snapshot_b = make_snapshot(
        inputs=[{"path": "inputs/a.txt", "hash": "sha256:aaa", "size": 1}],
        outputs=[{"path": "outputs/z.txt", "hash": "sha256:zzz", "size": 2}],
        profile_id="profile-1",
        profile_version="2024.04",
        device="cpu",
    )

    agent_headers = {
        "input_hash": "sha256:input",
        "model_version": "model-x",
        "contract_version": "1.0",
        "requested_model_version": "model-x",
        "resolved_model_version": "model-x",
        "schema_version": "1.0",
        "profile_id": "profile-1",
        "profile_version": "2024.04",
        "device": "cpu",
    }

    assert canonical_json_bytes(snapshot_a) == canonical_json_bytes(snapshot_b)
    assert canonical_json_bytes(make_receipt(snapshot_a, agent_headers=agent_headers)) == canonical_json_bytes(
        make_receipt(snapshot_b, agent_headers=agent_headers)
    )
