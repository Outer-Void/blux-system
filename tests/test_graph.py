"""Parent/child graphs and every status path: SUCCESS, FAILURE, PARTIAL, DENIED, CANCELLED."""

from __future__ import annotations

import pytest
from blux_sdk import ids

from blux_system.errors import StoreError, StoreErrorCode
from blux_system.graph import build_graph, timeline
from blux_system.statuses import Status, status_of_receipt
from blux_system.store import ReceiptStore
from tests.conftest import receipt


@pytest.mark.parametrize(
    ("status", "error_code", "expected"),
    [
        ("succeeded", None, Status.SUCCESS),
        ("failed", "EXECUTION_FAILED", Status.FAILURE),
        ("failed", "TIMEOUT", Status.FAILURE),
        ("failed", "OUTPUT_VALIDATION_FAILED", Status.FAILURE),
        ("rejected", "POLICY_DENIED", Status.DENIED),
        ("rejected", "MALFORMED_INPUT", Status.FAILURE),
        ("rejected", "UNSUPPORTED_CONTRACT_VERSION", Status.FAILURE),
    ],
)
def test_status_mapping(status: str, error_code: str | None, expected: Status) -> None:
    assert status_of_receipt(receipt(status=status, error_code=error_code)) is expected


def tree(store: ReceiptStore) -> dict[str, str]:
    """root
    ├── a (success)
    │   ├── a1 (success)
    │   └── a2 (failed)
    ├── b (denied)
    ├── c (cancelled, no receipt)
    └── d (success)
    """
    ses = ids.new_session_id()
    op = {k: ids.new_operation_id() for k in ("root", "a", "a1", "a2", "b", "c", "d")}
    store.ingest_receipt(receipt(operation_id=op["root"], session_id=ses, start=0, end=10))
    store.ingest_receipt(receipt(operation_id=op["a"], parent=op["root"], session_id=ses, start=1, end=5))
    store.ingest_receipt(receipt(operation_id=op["a1"], parent=op["a"], session_id=ses, start=2, end=3))
    store.ingest_receipt(
        receipt(operation_id=op["a2"], parent=op["a"], session_id=ses, status="failed", error_code="TIMEOUT", start=3, end=4)
    )
    store.ingest_receipt(
        receipt(operation_id=op["b"], parent=op["root"], session_id=ses, status="rejected", error_code="POLICY_DENIED", start=6, end=6)
    )
    store.cancel(op["c"], reason="user aborted", parent_operation_id=op["root"], session_id=ses)
    store.ingest_receipt(receipt(operation_id=op["d"], parent=op["root"], session_id=ses, start=8, end=9))
    op["session"] = ses
    return op


def test_parent_child_graph_and_aggregate_statuses(store: ReceiptStore) -> None:
    op = tree(store)
    g = build_graph(ReceiptStore.open(store.root))
    n = g["nodes"]
    assert g["roots"] == [op["root"]]
    assert g["cycles"] == []
    # children ordered by start time; the cancelled op has no start and sorts first
    assert n[op["root"]]["children"] == [op["c"], op["a"], op["b"], op["d"]]
    assert [op["a"], op["a1"]] in g["edges"] and [op["a"], op["a2"]] in g["edges"]
    own = {k: n[op[k]]["own_status"] for k in ("root", "a", "a1", "a2", "b", "c", "d")}
    agg = {k: n[op[k]]["aggregate_status"] for k in ("root", "a", "a1", "a2", "b", "c", "d")}
    assert own == {"root": "SUCCESS", "a": "SUCCESS", "a1": "SUCCESS", "a2": "FAILURE", "b": "DENIED", "c": "CANCELLED", "d": "SUCCESS"}
    assert agg == {"root": "PARTIAL", "a": "PARTIAL", "a1": "SUCCESS", "a2": "FAILURE", "b": "DENIED", "c": "CANCELLED", "d": "SUCCESS"}
    assert n[op["a2"]]["error_code"] == "TIMEOUT"
    assert n[op["c"]]["cancelled"]["reason"] == "user aborted"
    assert n[op["c"]]["receipt_id"] is None


def test_subtree_and_session_scoping(store: ReceiptStore) -> None:
    op = tree(store)
    store.ingest_receipt(receipt())  # unrelated, other session
    sub = build_graph(store, root_operation_id=op["a"])
    assert set(sub["nodes"]) == {op["a"], op["a1"], op["a2"]}
    assert sub["roots"] == [op["a"]]
    ses = build_graph(store, session_id=op["session"])
    assert len(ses["nodes"]) == 7
    assert len(build_graph(store)["nodes"]) == 8
    assert build_graph(store, root_operation_id=ids.new_operation_id())["nodes"] == {}


def test_all_success_tree_is_success(store: ReceiptStore) -> None:
    root, child = ids.new_operation_id(), ids.new_operation_id()
    store.ingest_receipt(receipt(operation_id=root))
    store.ingest_receipt(receipt(operation_id=child, parent=root))
    n = build_graph(store)["nodes"]
    assert n[root]["aggregate_status"] == n[child]["aggregate_status"] == "SUCCESS"


def test_missing_parent_becomes_placeholder(store: ReceiptStore) -> None:
    ghost, child = ids.new_operation_id(), ids.new_operation_id()
    store.ingest_receipt(receipt(operation_id=child, parent=ghost))
    g = build_graph(store)
    assert g["roots"] == [ghost]
    assert g["nodes"][ghost]["missing"] is True
    assert g["nodes"][ghost]["own_status"] is None and g["nodes"][ghost]["aggregate_status"] is None
    assert g["nodes"][ghost]["children"] == [child]
    assert g["nodes"][child]["aggregate_status"] == "SUCCESS"


def test_cycles_are_reported_not_dropped(store: ReceiptStore) -> None:
    a, b = ids.new_operation_id(), ids.new_operation_id()
    store.ingest_receipt(receipt(operation_id=a, parent=b))
    store.ingest_receipt(receipt(operation_id=b, parent=a))
    g = build_graph(store)
    assert g["cycles"] == [sorted([a, b])]
    assert set(g["roots"]) == {a, b}
    assert set(g["nodes"]) == {a, b}


def test_cancellation_rules(store: ReceiptStore) -> None:
    done = receipt()
    store.ingest_receipt(done)
    with pytest.raises(StoreError) as e:
        store.cancel(done["operation_id"], reason="too late")
    assert e.value.code is StoreErrorCode.ALREADY_FINISHED
    op = ids.new_operation_id()
    assert store.cancel(op, reason="stop")["status"] == "stored"
    assert store.cancel(op, reason="stop again")["status"] == "duplicate"
    with pytest.raises(StoreError) as e:
        store.ingest_receipt(receipt(operation_id=op))
    assert e.value.code is StoreErrorCode.ALREADY_FINISHED
    with pytest.raises(StoreError) as e:
        store.cancel("op_bad", reason="x")
    assert e.value.code is StoreErrorCode.INVALID_RECORD


def test_timeline_is_chronological_and_scoped(store: ReceiptStore) -> None:
    op = tree(store)
    items = timeline(store, operation_id=op["a"])
    assert {i["operation_id"] for i in items} == {op["a"], op["a1"], op["a2"]}
    assert [(i["type"], i["operation_id"]) for i in items][:2] == [("started", op["a"]), ("started", op["a1"])]
    fin = [i for i in items if i["type"] == "finished"]
    assert {i["status"] for i in fin} == {"SUCCESS", "FAILURE"}
    full = timeline(store, session_id=op["session"])
    assert any(i["type"] == "cancelled" and i["operation_id"] == op["c"] for i in full)
    ats = [i["at"] for i in timeline(store)]
    assert len(ats) >= 12
