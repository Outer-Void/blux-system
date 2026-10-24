from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from blux_sdk import envelopes, ids

from blux_system.errors import StoreError, StoreErrorCode
from blux_system.graph import build_graph
from blux_system.store import ReceiptStore
from tests.conftest import receipt


# ---------------------------------------------------------------- artifacts
def test_artifact_bytes_verified_and_blob_kept(store: ReceiptStore, tmp_path: Path) -> None:
    op = ids.new_operation_id()
    data = b"hello artifact"
    art = envelopes.artifact_from_bytes(operation_id=op, name="out.txt", media_type="text/plain", data=data)
    assert store.ingest_artifact(art, data)["status"] == "stored"
    assert store.verify_artifact(art["artifact_id"])["ok"] is True
    f = tmp_path / "copy.txt"
    f.write_bytes(data)
    assert store.verify_artifact(art["artifact_id"], f)["ok"] is True
    f.write_bytes(b"tampered")
    res = store.verify_artifact(art["artifact_id"], f)
    assert res["ok"] is False and res["actual_size"] == 8
    blob = store._blob_path(art["sha256"])
    blob.write_bytes(b"bitrot")
    assert store.verify_artifact(art["artifact_id"])["ok"] is False


def test_artifact_mismatch_rejected_and_linked_into_graph(store: ReceiptStore) -> None:
    op = ids.new_operation_id()
    art = envelopes.artifact_from_bytes(operation_id=op, name="a.bin", media_type="application/octet-stream", data=b"abc")
    with pytest.raises(StoreError) as e:
        store.ingest_artifact(art, b"abd")
    assert e.value.code is StoreErrorCode.ARTIFACT_HASH_MISMATCH
    assert store.index.artifacts == {}
    store.ingest_artifact(art)  # descriptor without bytes
    store.ingest_receipt(receipt(operation_id=op, artifact_ids=[art["artifact_id"]]))
    assert build_graph(store)["nodes"][op]["artifact_ids"] == [art["artifact_id"]]
    with pytest.raises(StoreError) as e:
        store.verify_artifact(art["artifact_id"])
    assert e.value.code is StoreErrorCode.NOT_FOUND


# ---------------------------------------------------------------- export / import
def test_export_import_roundtrip_and_idempotence(store: ReceiptStore, tmp_path: Path) -> None:
    root = ids.new_operation_id()
    store.ingest_receipt(receipt(operation_id=root))
    store.ingest_receipt(receipt(parent=root, status="failed"))
    store.cancel(ids.new_operation_id(), reason="stop", parent_operation_id=root)
    bundle = tmp_path / "b.jsonl"
    with bundle.open("wb") as fh:
        header = store.export(fh)
    assert header["records"] == 3
    other = ReceiptStore.init(tmp_path / "other")
    assert other.import_bundle(bundle.read_bytes()) == {"stored": 3, "duplicate": 0, "skipped": 0}
    assert other.import_bundle(bundle.read_bytes()) == {"stored": 0, "duplicate": 3, "skipped": 0}
    g1, g2 = build_graph(store), build_graph(other)
    strip = lambda g: {k: {kk: vv for kk, vv in v.items() if kk != "cancelled"} for k, v in g["nodes"].items()}  # noqa: E731
    assert strip(g1) == strip(g2)
    assert ReceiptStore.open(other.root).verify().ok


@pytest.mark.parametrize("damage", ["edit", "drop", "header", "empty", "notjson"])
def test_tampered_bundle_rejected(store: ReceiptStore, tmp_path: Path, damage: str) -> None:
    store.ingest_receipt(receipt())
    store.ingest_receipt(receipt())
    bundle = tmp_path / "b.jsonl"
    with bundle.open("wb") as fh:
        store.export(fh)
    lines = bundle.read_bytes().splitlines()
    if damage == "edit":
        rec = json.loads(lines[1])
        rec["payload"]["capability_id"] = "evil.cap"
        lines[1] = json.dumps(rec).encode()
    elif damage == "drop":
        del lines[2]
    elif damage == "header":
        lines[0] = b'{"format": "x"}'
    elif damage == "empty":
        lines = []
    elif damage == "notjson":
        lines[1] = b"{oops"
    other = ReceiptStore.init(tmp_path / "o")
    with pytest.raises(StoreError) as e:
        other.import_bundle(b"\n".join(lines))
    assert e.value.code is StoreErrorCode.IMPORT_INVALID
    assert other.index.records == []


# ---------------------------------------------------------------- retention
def test_retention_compacts_old_records_and_keeps_chain(tmp_path: Path) -> None:
    policy = {"contract": "blux-system.retention", "version": 1, "keep_days": {"receipt": 30, "event": 1, "artifact": None}}
    store = ReceiptStore.init(tmp_path / "s", policy=policy)
    old, new = receipt(), receipt()
    store.ingest_receipt(old)  # finished 2026-10-24
    store.ingest_receipt(new)
    store.cancel(ids.new_operation_id(), reason="x")
    later = datetime(2026, 11, 30, tzinfo=timezone.utc)
    # make "new" fresh by advancing nothing: both receipts are from 2026-10-24, cutoff 2026-10-31 -> both removed
    res = store.apply_retention(now=later)
    assert res["removed"] == {"receipt": 2, "event": 1}
    reopened = ReceiptStore.open(store.root)
    assert reopened.verify().ok
    assert reopened.receipts() == []
    assert reopened.index.records[-1]["kind"] == "retention"
    assert reopened.index.records[-1]["payload"]["removed"] == {"receipt": 2, "event": 1}


def test_retention_keeps_recent_and_is_noop_without_limits(store: ReceiptStore, tmp_path: Path) -> None:
    store.ingest_receipt(receipt())
    assert store.apply_retention() == {"removed": {}, "kept": 1}
    policy = {"contract": "blux-system.retention", "version": 1, "keep_days": {"receipt": 30}}
    s = ReceiptStore.init(tmp_path / "s2", policy=policy)
    r = receipt()
    s.ingest_receipt(r)
    assert s.apply_retention(now=datetime(2026, 10, 25, tzinfo=timezone.utc))["removed"] == {}
    assert s.get_receipt(r["receipt_id"]) == r


# ---------------------------------------------------------------- replay
def test_replay_metadata_and_verification(store: ReceiptStore) -> None:
    out = {"message": "HI", "length": 2, "uppercase": True}
    r = receipt(output=out)
    store.ingest_receipt(r)
    meta = store.replay_metadata(r["operation_id"])
    assert meta["replayable"] is True and meta["capability_version"] == "1.0.0"
    assert store.verify_replay(r["operation_id"], output_doc=out)["ok"] is True
    assert store.verify_replay(r["operation_id"], output_doc={**out, "length": 3})["ok"] is False
    assert store.verify_replay(r["operation_id"], input_doc={"message": "hi"}, output_doc=out) == {
        "operation_id": r["operation_id"],
        "input_match": True,
        "output_match": True,
        "ok": True,
    }
    failed = receipt(status="failed")
    store.ingest_receipt(failed)
    assert store.replay_metadata(failed["operation_id"])["replayable"] is False
