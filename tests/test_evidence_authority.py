"""OV-52: BLUX System as the evidence authority. Tampering and corruption are detected, never crash a reader,
and no crash or failure leaves an unrecorded rewrite or a half-done import.

Each test failed on f7f8ede:
- a record whose newline was not written made the next append corrupt the whole store;
- whole records cut from the end of the log, or an older copy of the log, verified as ok;
- a re-hashed record with a malformed payload crashed every command with a traceback;
- a damaged retention.json or store.json crashed instead of STORE_CORRUPT;
- an import could stop part-way, and accepted malformed events;
- tampered or missing artifact blobs verified as ok, and re-ingesting the bytes did not repair them;
- simultaneous writers were refused (STORE_LOCKED) instead of queued;
- a retention run killed after its rewrite left no record of what it removed.
"""

from __future__ import annotations

import io
import json
import threading
from pathlib import Path
from typing import Any

import pytest
from blux_sdk import envelopes, ids
from blux_sdk.serialization import canonical_json

from blux_system import store as store_mod
from blux_system.cli import main
from blux_system.errors import StoreError, StoreErrorCode
from blux_system.store import ReceiptStore, _record_hash
from tests.conftest import receipt


def cli(*argv: str) -> tuple[int, dict[str, Any]]:
    out = io.StringIO()
    code = main(list(argv), stdout=out)
    return code, json.loads(out.getvalue())


def filled(store: ReceiptStore, n: int = 3) -> list[dict[str, Any]]:
    rs = [receipt() for _ in range(n)]
    for r in rs:
        store.ingest_receipt(r)
    return rs


def forge(store: ReceiptStore, kind: str, payload: Any) -> None:
    """Append a record with a correct hash chain, as a program that recomputes hashes could."""
    body = {
        "seq": len(store.index.records) + 1,
        "kind": kind,
        "at": "2026-11-07T00:00:00.000Z",
        "payload": payload,
        "meta": {},
        "prev": store.index.head,
    }
    with store.log_path.open("ab") as fh:
        fh.write((canonical_json({**body, "hash": _record_hash(body)}) + "\n").encode())


def corrupt_reason(root: Path) -> list[str]:
    with pytest.raises(StoreError) as exc:
        ReceiptStore.open(root, read_only=True)
    assert exc.value.code is StoreErrorCode.STORE_CORRUPT
    return [p["reason"] for p in exc.value.details.get("problems", [])] or [exc.value.details.get("reason", "")]


# ---------------------------------------------------------------- crash consistency
def test_a_record_written_without_its_newline_is_completed_not_a_corrupt_store(store: ReceiptStore) -> None:
    rs = filled(store)
    store.log_path.write_bytes(store.log_path.read_bytes()[:-1])  # crash between the record and its "\n"
    writer = ReceiptStore.open(store.root)
    late = receipt()
    assert writer.ingest_receipt(late)["status"] == "stored"
    again = ReceiptStore.open(store.root)
    assert again.verify().ok
    assert {r["receipt_id"] for r in again.receipts()} == {r["receipt_id"] for r in [*rs, late]}
    assert [r["payload"]["reason"] for r in again.index.records if r["kind"] == "recovery"] == ["unterminated_record"]


def test_a_torn_tail_left_by_another_writer_is_repaired_by_a_long_lived_writer(store: ReceiptStore) -> None:
    filled(store, 1)
    with store.log_path.open("ab") as fh:
        fh.write(b'{"seq": 2, "kind": "rec')  # another process crashed mid-append
    assert store.ingest_receipt(receipt())["status"] == "stored"
    assert ReceiptStore.open(store.root).verify().ok


def test_simultaneous_writers_queue_instead_of_being_refused(store: ReceiptStore) -> None:
    writers = [ReceiptStore.open(store.root) for _ in range(12)]
    barrier, results = threading.Barrier(len(writers)), []

    def go(s: ReceiptStore) -> None:
        r = receipt()
        barrier.wait()
        try:
            results.append(s.ingest_receipt(r)["status"])
        except StoreError as exc:
            results.append(exc.code.value)

    threads = [threading.Thread(target=go, args=(s,)) for s in writers]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == ["stored"] * len(writers)
    assert len(ReceiptStore.open(store.root).receipts()) == len(writers)


def test_a_writer_still_reports_store_locked_after_its_wait(store: ReceiptStore) -> None:
    holder = ReceiptStore.open(store.root)
    impatient = ReceiptStore.open(store.root, lock_wait_s=0.05)
    with holder._lock(), pytest.raises(StoreError) as exc:
        impatient.ingest_receipt(receipt())
    assert exc.value.code is StoreErrorCode.STORE_LOCKED


# ---------------------------------------------------------------- tampering
def test_records_cut_from_the_end_are_detected(store: ReceiptStore) -> None:
    filled(store)
    lines = store.log_path.read_bytes().splitlines(keepends=True)
    store.log_path.write_bytes(b"".join(lines[:1]))
    assert corrupt_reason(store.root) == ["truncated"]
    code, doc = cli("verify", "--store", str(store.root))
    assert code == 1 and doc["problems"][0]["reason"] == "truncated"


def test_an_older_copy_of_the_log_is_detected(store: ReceiptStore) -> None:
    filled(store, 2)
    old = store.log_path.read_bytes()
    filled(store, 2)
    store.log_path.write_bytes(old)  # e.g. restored from a stale backup
    assert corrupt_reason(store.root) == ["truncated"]


def test_a_swapped_log_of_the_same_length_is_detected(store: ReceiptStore, tmp_path: Path) -> None:
    filled(store, 2)
    other = ReceiptStore.init(tmp_path / "other")
    filled(other, 2)
    store.log_path.write_bytes(other.log_path.read_bytes())
    assert corrupt_reason(store.root) == ["anchor_mismatch"]


@pytest.mark.parametrize(
    ("kind", "payload"),
    [
        ("receipt", {"x": 1}),
        ("receipt", "not an object"),
        ("artifact", {"contract": "blux.artifact", "artifact_id": "a"}),
        ("event", {"event_id": "evt_x"}),
    ],
)
def test_a_rehashed_malformed_record_is_corrupt_not_a_crash(store: ReceiptStore, kind: str, payload: Any) -> None:
    filled(store, 1)
    forge(store, kind, payload)
    assert corrupt_reason(store.root) == ["invalid_payload"]
    for command in ("verify", "list", "graph"):
        code, doc = cli(command, "--store", str(store.root))
        assert code == 1, command
        assert doc.get("code") == "STORE_CORRUPT" or doc["problems"][0]["reason"] == "invalid_payload"


def test_an_unknown_receipt_status_no_longer_crashes_the_graph(store: ReceiptStore) -> None:
    bad = receipt()
    bad["status"] = "weird"
    forge(store, "receipt", bad)
    code, doc = cli("graph", "--store", str(store.root))
    assert (code, doc["code"]) == (1, "STORE_CORRUPT")


def test_a_receipt_rejected_before_its_capability_was_known_is_valid(store: ReceiptStore) -> None:
    # Guards against a false positive: capability_id and capability_version are null on such receipts.
    r = receipt(status="rejected", error_code="UNSUPPORTED_CONTRACT_VERSION")
    r["capability_id"] = r["capability_version"] = None
    store.ingest_receipt(r)
    reopened = ReceiptStore.open(store.root)
    assert reopened.verify().ok and reopened.get_receipt(r["receipt_id"]) == r


def test_full_verify_checks_payloads_against_the_contract(store: ReceiptStore) -> None:
    bad = receipt()
    bad["input_sha256"] = "not-a-hash"  # structurally readable, but not Contract v1
    forge(store, "receipt", bad)
    assert ReceiptStore.open(store.root, read_only=True).receipts()  # readers keep working
    report = ReceiptStore.open(store.root, read_only=True).verify()
    assert not report.ok and report.problems[0]["reason"] == "invalid_payload"


@pytest.mark.parametrize(
    ("file", "content", "reason"),
    [
        ("retention.json", "{nope", "JSONDecodeError"),
        ("store.json", "[]", "not_a_store"),
        ("store.json", '{"format": "blux-system-store", "version": 2}', "unsupported_version"),
    ],
)
def test_damaged_or_newer_metadata_is_store_corrupt(store: ReceiptStore, file: str, content: str, reason: str) -> None:
    (store.root / file).write_text(content)
    for command in ("list", "verify"):
        code, doc = cli(command, "--store", str(store.root))
        assert (code, doc["code"], doc["details"]["reason"]) == (1, "STORE_CORRUPT", reason), command


def test_expect_head_ties_the_store_to_an_external_anchor(store: ReceiptStore, tmp_path: Path) -> None:
    filled(store)
    code, header = cli("export", str(tmp_path / "b.jsonl"), "--store", str(store.root))
    assert code == 0
    assert cli("verify", "--store", str(store.root), "--expect-head", header["head"])[0] == 0
    filled(store, 1)
    assert cli("verify", "--store", str(store.root), "--expect-head", header["head"])[0] == 0  # still in the chain
    code, doc = cli("verify", "--store", str(store.root), "--expect-head", "f" * 64)
    assert code == 1 and doc["problems"] == [{"reason": "expected_head_not_found", "expected_head": "f" * 64}]


def test_a_store_from_before_ov52_opens_and_gains_an_anchor(store: ReceiptStore) -> None:
    filled(store)
    store.head_path.unlink()  # no anchor yet
    s = ReceiptStore.open(store.root)
    assert s.verify().ok and not store.head_path.exists()
    s.ingest_receipt(receipt())
    assert json.loads(store.head_path.read_text())["seq"] == 4


def test_records_appended_without_the_anchor_are_accepted(store: ReceiptStore) -> None:
    filled(store, 2)
    anchor = store.head_path.read_bytes()
    filled(store, 2)
    store.head_path.write_bytes(anchor)  # a writer from before OV-52, or a crash before the anchor update
    assert ReceiptStore.open(store.root).verify().ok


# ---------------------------------------------------------------- artifacts
def _artifact(data: bytes) -> dict[str, Any]:
    return envelopes.artifact_from_bytes(operation_id=ids.new_operation_id(), name="a.txt", media_type="text/plain", data=data)


def test_tampered_or_missing_blobs_fail_verify_and_re_ingest_repairs_them(store: ReceiptStore) -> None:
    data = b"hello artifact"
    art = _artifact(data)
    store.ingest_artifact(art, data)
    blob = store._blob_path(art["sha256"])
    blob.write_bytes(b"tampered!!!!!!")
    code, doc = cli("verify", "--store", str(store.root))
    assert code == 1 and doc["problems"] == [{"seq": 1, "reason": "blob_mismatch", "artifact_id": art["artifact_id"]}]
    assert store.ingest_artifact(art, data)["status"] == "duplicate"
    assert blob.read_bytes() == data and store.verify().ok
    blob.unlink()
    assert [p["reason"] for p in store.verify().problems] == ["blob_missing"]


# ---------------------------------------------------------------- import
def _bundle(path: Path, payloads: list[tuple[str, dict[str, Any]]]) -> Path:
    prev, lines = "0" * 64, []
    for i, (kind, payload) in enumerate(payloads, 1):
        body = {"seq": i, "kind": kind, "at": "2026-11-07T00:00:00.000Z", "payload": payload, "meta": {}, "prev": prev}
        prev = _record_hash(body)
        lines.append(canonical_json({**body, "hash": prev}))
    header = {"format": "blux-system-export", "version": 1, "records": len(lines), "head": prev}
    path.write_text("\n".join([canonical_json(header), *lines]) + "\n")
    return path


@pytest.mark.parametrize(
    "event",
    [
        {"event_id": "evt_x"},
        {"event_id": "evt_x", "type": "note", "operation_id": "nonsense", "data": {}},
        {"event_id": "evt_x", "type": "launch", "operation_id": "", "data": {}},
    ],
)
def test_malformed_imported_events_are_refused(store: ReceiptStore, tmp_path: Path, event: dict[str, Any]) -> None:
    bundle = _bundle(tmp_path / "b.jsonl", [("receipt", receipt()), ("event", event)])
    code, doc = cli("import", str(bundle), "--store", str(store.root))
    assert (code, doc["code"]) == (1, "IMPORT_INVALID")
    assert ReceiptStore.open(store.root).index.records == []


def test_an_import_that_would_conflict_imports_nothing(store: ReceiptStore, tmp_path: Path) -> None:
    first, second = receipt(), receipt()
    store.ingest_receipt(receipt(operation_id=second["operation_id"]))
    bundle = _bundle(tmp_path / "b.jsonl", [("receipt", first), ("receipt", second)])
    code, doc = cli("import", str(bundle), "--store", str(store.root))
    assert (code, doc["code"]) == (1, "RECEIPT_CONFLICT")
    assert doc["details"]["imported_before_failure"] == {"stored": 0}
    assert len(ReceiptStore.open(store.root).receipts()) == 1  # `first` was not imported either


def test_conflicts_inside_one_bundle_import_nothing(store: ReceiptStore, tmp_path: Path) -> None:
    a = receipt()
    b = receipt(operation_id=a["operation_id"])
    bundle = _bundle(tmp_path / "b.jsonl", [("receipt", receipt()), ("receipt", a), ("receipt", b)])
    assert cli("import", str(bundle), "--store", str(store.root))[1]["code"] == "RECEIPT_CONFLICT"
    assert ReceiptStore.open(store.root).receipts() == []


# ---------------------------------------------------------------- retention
POLICY = {"contract": "blux-system.retention", "version": 1, "keep_days": {"receipt": 1, "event": None, "artifact": None}}


@pytest.fixture
def expiring(tmp_path: Path) -> ReceiptStore:
    s = ReceiptStore.init(tmp_path / "ret", policy=POLICY)
    filled(s, 2)  # dated 2026-10-24 by the fixture: expired
    return s


def test_retention_records_itself_inside_the_new_chain(expiring: ReceiptStore) -> None:
    before = expiring.index.head
    assert expiring.apply_retention()["removed"] == {"receipt": 2}
    s = ReceiptStore.open(expiring.root)
    assert s.verify().ok
    (rec,) = s.index.records
    assert rec["kind"] == "retention"
    assert rec["payload"]["previous_head"] == before and rec["payload"]["previous_records"] == 2


def test_retention_killed_before_its_rename_leaves_the_old_log(expiring: ReceiptStore, monkeypatch: pytest.MonkeyPatch) -> None:
    real = store_mod.os.replace

    def killed(src: Any, dst: Any) -> None:
        if Path(dst) == expiring.log_path:
            raise SystemExit("killed")
        real(src, dst)

    monkeypatch.setattr(store_mod.os, "replace", killed)
    with pytest.raises(SystemExit):
        expiring.apply_retention()
    monkeypatch.undo()
    s = ReceiptStore.open(expiring.root)
    assert s.verify().ok and len(s.receipts()) == 2


def test_retention_killed_after_its_rename_is_still_recorded(expiring: ReceiptStore, monkeypatch: pytest.MonkeyPatch) -> None:
    real = ReceiptStore._write_head

    def killed(self: ReceiptStore, pending: dict[str, Any] | None = None) -> None:
        if pending is None:
            raise SystemExit("killed")  # after the rename, before the final anchor
        real(self, pending)

    monkeypatch.setattr(ReceiptStore, "_write_head", killed)
    with pytest.raises(SystemExit):
        expiring.apply_retention()
    monkeypatch.undo()
    s = ReceiptStore.open(expiring.root)
    assert s.verify().ok
    assert [r["kind"] for r in s.index.records] == ["retention"]
