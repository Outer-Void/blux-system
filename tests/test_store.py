from __future__ import annotations

import copy
from pathlib import Path

import pytest
from blux_sdk import ids

from blux_system.errors import StoreError, StoreErrorCode
from blux_system.store import ReceiptStore
from tests.conftest import receipt


def code(exc: pytest.ExceptionInfo[StoreError]) -> StoreErrorCode:
    return exc.value.code


def test_ingest_persists_across_reopen(store: ReceiptStore) -> None:
    r = receipt()
    res = store.ingest_receipt(r)
    assert res["status"] == "stored" and res["seq"] == 1
    again = ReceiptStore.open(store.root)
    assert again.get_receipt(r["receipt_id"]) == r
    assert again.receipt_for_operation(r["operation_id"]) == r
    assert again.verify().ok


def test_identical_reingest_is_idempotent(store: ReceiptStore) -> None:
    r = receipt()
    store.ingest_receipt(r)
    assert store.ingest_receipt(copy.deepcopy(r))["status"] == "duplicate"
    assert len(store.index.records) == 1


def test_receipts_are_immutable(store: ReceiptStore) -> None:
    r = receipt()
    store.ingest_receipt(r)
    changed = {**r, "finished_at": "2026-10-24T00:00:09.000Z"}
    with pytest.raises(StoreError) as e:
        store.ingest_receipt(changed)
    assert code(e) is StoreErrorCode.RECEIPT_CONFLICT
    second = receipt(operation_id=r["operation_id"])
    with pytest.raises(StoreError) as e:
        store.ingest_receipt(second)
    assert code(e) is StoreErrorCode.RECEIPT_CONFLICT
    assert e.value.details["operation_id"] == r["operation_id"]


@pytest.mark.parametrize(
    ("mutate", "contract_code"),
    [
        (lambda r: r.update(contract_version="2.0"), "UNSUPPORTED_CONTRACT_VERSION"),
        (lambda r: r.update(contract_version=1.0), "MALFORMED_CONTRACT_VERSION"),
        (lambda r: r.update(contract="blux.result"), "INVALID_ENVELOPE"),
        (lambda r: r.update(status="done"), "SCHEMA_VALIDATION_FAILED"),
        (lambda r: r.pop("receipt_id"), "SCHEMA_VALIDATION_FAILED"),
    ],
)
def test_invalid_receipts_are_rejected_with_contract_code(store: ReceiptStore, mutate, contract_code: str) -> None:  # type: ignore[no-untyped-def]
    r = receipt()
    mutate(r)
    with pytest.raises(StoreError) as e:
        store.ingest_receipt(r)
    assert code(e) is StoreErrorCode.INVALID_RECORD
    assert e.value.details["contract_error"]["code"] == contract_code
    assert store.index.records == []


def test_open_and_init_errors(tmp_path: Path, store: ReceiptStore) -> None:
    with pytest.raises(StoreError) as e:
        ReceiptStore.open(tmp_path / "nope")
    assert code(e) is StoreErrorCode.STORE_NOT_FOUND
    with pytest.raises(StoreError) as e:
        ReceiptStore.init(store.root)
    assert code(e) is StoreErrorCode.STORE_EXISTS


def test_read_only_store_refuses_writes(store: ReceiptStore) -> None:
    ro = ReceiptStore.open(store.root, read_only=True)
    with pytest.raises(StoreError) as e:
        ro.ingest_receipt(receipt())
    assert code(e) is StoreErrorCode.STORE_READ_ONLY


def test_concurrent_writer_is_refused(store: ReceiptStore) -> None:
    fcntl = pytest.importorskip("fcntl")
    with (store.root / ".lock").open("a+b") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        other = ReceiptStore.open(store.root)
        with pytest.raises(StoreError) as e:
            other.ingest_receipt(receipt())
        assert code(e) is StoreErrorCode.STORE_LOCKED


def test_second_handle_sees_first_handles_writes(store: ReceiptStore) -> None:
    other = ReceiptStore.open(store.root)
    r1, r2 = receipt(), receipt()
    store.ingest_receipt(r1)
    other.ingest_receipt(r2)  # reloads under the lock, so the chain does not fork
    store.ingest_receipt(receipt())
    final = ReceiptStore.open(store.root)
    assert final.verify().ok and len(final.index.records) == 3


def test_query_filters(store: ReceiptStore) -> None:
    ses = ids.new_session_id()
    a = receipt(session_id=ses, start=1, end=2)
    b = receipt(status="failed", start=3, end=4)
    c = receipt(status="rejected", error_code="POLICY_DENIED", capability_id="fs.delete", session_id=ses, start=5, end=6)
    for r in (a, b, c):
        store.ingest_receipt(r)
    assert [r["receipt_id"] for r in store.receipts(session_id=ses)] == [a["receipt_id"], c["receipt_id"]]
    assert [r["receipt_id"] for r in store.receipts(status="FAILURE")] == [b["receipt_id"]]
    assert [r["receipt_id"] for r in store.receipts(status="DENIED")] == [c["receipt_id"]]
    assert [r["receipt_id"] for r in store.receipts(capability_id="fs.delete")] == [c["receipt_id"]]
    assert [r["receipt_id"] for r in store.receipts(since="2026-10-24T00:00:02.500Z", until="2026-10-24T00:00:04.000Z")] == [
        b["receipt_id"]
    ]
    with pytest.raises(StoreError) as e:
        store.receipts(since="yesterday")
    assert code(e) is StoreErrorCode.INVALID_RECORD
    with pytest.raises(StoreError) as e:
        store.get_receipt(ids.new_receipt_id())
    assert code(e) is StoreErrorCode.NOT_FOUND


def test_invalid_policy_rejected(tmp_path: Path) -> None:
    for bad in (
        {"contract": "nope"},
        {"contract": "blux-system.retention", "version": 1, "keep_days": {"receipt": 0}},
        {"contract": "blux-system.retention", "version": 1, "keep_days": {"logs": 1}},
        {"contract": "blux-system.retention", "version": 1, "redact": {"extra_patterns": {"bad": "("}}},
        {"contract": "blux-system.retention", "version": 1, "surprise": True},
    ):
        with pytest.raises(StoreError) as e:
            ReceiptStore.init(tmp_path / "s", policy=bad)
        assert code(e) is StoreErrorCode.INVALID_POLICY
