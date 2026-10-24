"""Corruption and crash handling of the append-only log."""

from __future__ import annotations

import json

import pytest

from blux_system.errors import StoreError, StoreErrorCode
from blux_system.store import ReceiptStore
from tests.conftest import receipt


def filled(store: ReceiptStore, n: int = 3) -> list[dict]:  # type: ignore[type-arg]
    rs = [receipt() for _ in range(n)]
    for r in rs:
        store.ingest_receipt(r)
    return rs


def test_torn_tail_is_repaired_and_recorded(store: ReceiptStore) -> None:
    rs = filled(store)
    with store.log_path.open("ab") as fh:
        fh.write(b'{"seq": 4, "kind": "receipt", "payl')  # crash mid-append
    ro = ReceiptStore.open(store.root, read_only=True)
    assert len(ro.index.receipts) == 3  # intact prefix readable without writing
    assert ro.verify().torn_tail
    fixed = ReceiptStore.open(store.root)
    assert fixed.verify().ok
    assert fixed.index.records[-1]["kind"] == "recovery"
    assert fixed.index.records[-1]["payload"]["reason"] == "torn_tail"
    assert {r["receipt_id"] for r in fixed.receipts()} == {r["receipt_id"] for r in rs}


@pytest.mark.parametrize(
    ("damage", "reason"),
    [
        ("edit_payload", "hash_mismatch"),
        ("delete_middle", "sequence_gap"),
        ("garbage_middle", "unparseable"),
        ("swap", "sequence_gap"),
    ],
)
def test_mid_log_corruption_is_refused(store: ReceiptStore, damage: str, reason: str) -> None:
    filled(store)
    lines = store.log_path.read_bytes().splitlines(keepends=True)
    if damage == "edit_payload":
        rec = json.loads(lines[1])
        rec["payload"]["capability_id"] = "evil.cap"
        lines[1] = (json.dumps(rec) + "\n").encode()
    elif damage == "delete_middle":
        del lines[1]
    elif damage == "garbage_middle":
        lines[1] = b"not json at all\n"
    elif damage == "swap":
        lines[0], lines[1] = lines[1], lines[0]
    store.log_path.write_bytes(b"".join(lines))
    with pytest.raises(StoreError) as e:
        ReceiptStore.open(store.root)
    assert e.value.code is StoreErrorCode.STORE_CORRUPT
    problems = e.value.details["problems"]
    assert problems[0]["reason"] == reason
    report = ReceiptStore(store.root, read_only=True).verify()
    assert not report.ok and report.records == (0 if damage == "swap" else 1)


def test_rewritten_chain_with_valid_hashes_still_breaks_prev_link(store: ReceiptStore) -> None:
    """Re-hashing an edited record is not enough: the next record's prev no longer matches."""
    import hashlib

    from blux_sdk.serialization import canonical_json

    filled(store)
    lines = store.log_path.read_bytes().splitlines(keepends=True)
    rec = json.loads(lines[0])
    rec["payload"]["capability_id"] = "evil.cap"
    body = {k: rec[k] for k in ("seq", "kind", "at", "payload", "meta", "prev")}
    rec["hash"] = hashlib.sha256(canonical_json(body).encode()).hexdigest()
    lines[0] = (json.dumps(rec) + "\n").encode()
    store.log_path.write_bytes(b"".join(lines))
    with pytest.raises(StoreError) as e:
        ReceiptStore.open(store.root)
    assert e.value.details["problems"][0]["reason"] == "broken_chain"


def test_corrupt_marker(store: ReceiptStore) -> None:
    (store.root / "store.json").write_text("{nope")
    with pytest.raises(StoreError) as e:
        ReceiptStore.open(store.root)
    assert e.value.code is StoreErrorCode.STORE_CORRUPT
    (store.root / "store.json").write_text('{"format": "other", "version": 1}')
    with pytest.raises(StoreError) as e:
        ReceiptStore.open(store.root)
    assert e.value.code is StoreErrorCode.STORE_CORRUPT


def test_external_change_detected_before_next_write(store: ReceiptStore) -> None:
    filled(store, 2)
    lines = store.log_path.read_bytes().splitlines(keepends=True)
    store.log_path.write_bytes(lines[0] + b"x" * len(lines[1]) + b"\n")
    with pytest.raises(StoreError) as e:
        store.ingest_receipt(receipt())
    assert e.value.code is StoreErrorCode.STORE_CORRUPT
