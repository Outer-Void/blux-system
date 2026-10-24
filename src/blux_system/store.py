"""Local-first, append-only, hash-chained execution truth store.

Layout of a store directory:
    store.json        format marker
    retention.json    retention/privacy policy (blux-system.retention v1)
    log.jsonl         append-only records; each line chains to the previous record's hash
    blobs/sha256/..   optional content-addressed artifact bytes
    .pseudonym.key    per-store HMAC key, created only when actor pseudonymization is enabled
    .lock             writer lock

Records are never edited in place. Receipts are immutable: re-ingesting an identical receipt is a no-op,
a different document under the same receipt_id or operation_id is RECEIPT_CONFLICT. The only rewrite is an
explicit retention compaction, which itself is recorded.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import os
import secrets
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, BinaryIO

from blux_sdk import ids
from blux_sdk.errors import BluxError
from blux_sdk.schemas import check_envelope
from blux_sdk.serialization import canonical_json, loads_json, sha256_of
from blux_sdk.timeutil import parse_timestamp, utc_now

from blux_system.errors import StoreError, StoreErrorCode
from blux_system.retention import DEFAULT_POLICY, RetentionPolicy
from blux_system.statuses import status_of_receipt

try:  # POSIX advisory locking; other platforms run without it (single-writer by convention)
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

FORMAT = "blux-system-store"
FORMAT_VERSION = 1
GENESIS = "0" * 64
KINDS = ("receipt", "artifact", "event", "retention", "recovery")
EVENT_TYPES = ("cancelled", "note")


def _record_hash(body: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def new_event_id() -> str:
    return f"evt_{ids.ulid()}"


@dataclass
class Index:
    records: list[dict[str, Any]] = field(default_factory=list)
    receipts: dict[str, dict[str, Any]] = field(default_factory=dict)  # receipt_id -> record
    by_operation: dict[str, str] = field(default_factory=dict)  # operation_id -> receipt_id
    artifacts: dict[str, dict[str, Any]] = field(default_factory=dict)  # artifact_id -> record
    events: list[dict[str, Any]] = field(default_factory=list)
    head: str = GENESIS


@dataclass(frozen=True)
class VerifyReport:
    ok: bool
    records: int
    head: str
    problems: list[dict[str, Any]]
    torn_tail: bool

    def to_document(self) -> dict[str, Any]:
        return {"ok": self.ok, "records": self.records, "head": self.head, "problems": self.problems, "torn_tail": self.torn_tail}


class ReceiptStore:
    """Open with ReceiptStore.open(path) or create with ReceiptStore.init(path)."""

    def __init__(self, root: Path, *, read_only: bool) -> None:
        self.root = root
        self.read_only = read_only
        self.log_path = root / "log.jsonl"
        self.policy = RetentionPolicy.from_document(json.loads((root / "retention.json").read_text(encoding="utf-8")))
        self._redactor = self.policy.redactor()
        self.index = Index()

    # ------------------------------------------------------------------ lifecycle
    @classmethod
    def init(cls, path: str | Path, *, policy: dict[str, Any] | None = None) -> ReceiptStore:
        root = Path(path)
        if (root / "store.json").exists():
            raise StoreError(StoreErrorCode.STORE_EXISTS, "A store already exists here.", details={"path": str(root)})
        pol = RetentionPolicy.from_document(policy if policy is not None else DEFAULT_POLICY)
        root.mkdir(parents=True, exist_ok=True)
        (root / "store.json").write_text(canonical_json({"format": FORMAT, "version": FORMAT_VERSION, "created_at": utc_now()}) + "\n")
        (root / "retention.json").write_text(json.dumps(pol.to_document(), indent=2, sort_keys=True) + "\n")
        (root / "log.jsonl").touch()
        return cls.open(root)

    @classmethod
    def open(cls, path: str | Path, *, read_only: bool = False, repair_torn_tail: bool = True) -> ReceiptStore:
        root = Path(path)
        marker = root / "store.json"
        if not marker.is_file():
            raise StoreError(StoreErrorCode.STORE_NOT_FOUND, "No BLUX System store here.", details={"path": str(root)})
        try:
            meta = json.loads(marker.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raise StoreError(StoreErrorCode.STORE_CORRUPT, "store.json is not valid JSON.") from None
        if meta.get("format") != FORMAT or meta.get("version") != FORMAT_VERSION:
            raise StoreError(
                StoreErrorCode.STORE_CORRUPT,
                "Unrecognized store format.",
                details={"format": meta.get("format"), "version": meta.get("version")},
            )
        store = cls(root, read_only=read_only)
        report = store._load()
        only_torn = report.torn_tail and all(p["reason"] == "torn_tail" for p in report.problems)
        if report.ok:
            return store
        if only_torn and not read_only and repair_torn_tail:
            store._repair_torn_tail(report)
            return store
        if only_torn and read_only:
            return store  # intact prefix is readable; a writer will repair the tail
        raise StoreError(StoreErrorCode.STORE_CORRUPT, "Store log failed verification.", details=report.to_document())

    # ------------------------------------------------------------------ log reading / verification
    def _iter_lines(self) -> Iterator[tuple[int, bytes, bool]]:
        """Yield (byte_offset, raw_line, has_newline)."""
        with self.log_path.open("rb") as fh:
            offset = 0
            for raw in fh:
                yield offset, raw, raw.endswith(b"\n")
                offset += len(raw)

    def verify(self) -> VerifyReport:
        return _IndexBuilder(self).build()

    def _load(self) -> VerifyReport:
        builder = _IndexBuilder(self)
        report = builder.build()
        self.index = builder.index
        self._good_offset = builder.good_offset
        self._log_size = self.log_path.stat().st_size
        return report

    def _repair_torn_tail(self, report: VerifyReport) -> None:
        """A crash mid-append leaves an unterminated last line. Drop exactly that line and record the recovery."""
        dropped = self.log_path.stat().st_size - self._good_offset
        with self._lock(), self.log_path.open("r+b") as fh:
            fh.truncate(self._good_offset)
            fh.flush()
            os.fsync(fh.fileno())
        self._append("recovery", {"reason": "torn_tail", "dropped_bytes": dropped})

    # ------------------------------------------------------------------ writing
    @contextlib.contextmanager
    def _lock(self) -> Iterator[None]:
        if self.read_only:
            raise StoreError(StoreErrorCode.STORE_READ_ONLY, "Store was opened read-only.")
        with (self.root / ".lock").open("a+b") as lf:
            if fcntl is not None:
                try:
                    fcntl.flock(lf.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise StoreError(StoreErrorCode.STORE_LOCKED, "Another writer holds the store lock.") from None
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(lf.fileno(), fcntl.LOCK_UN)

    def _append(
        self,
        kind: str,
        payload: dict[str, Any],
        meta: dict[str, Any] | None = None,
        check: Callable[[], dict[str, Any] | None] | None = None,
    ) -> dict[str, Any]:
        """Append one record. `check` runs under the lock after reloading; a non-None return skips the write."""
        with self._lock():
            # Re-read the head under the lock so concurrent processes cannot fork the chain or race a conflict check.
            self._load_if_changed()
            if check is not None:
                skip = check()
                if skip is not None:
                    return skip
            body = {
                "seq": len(self.index.records) + 1,
                "kind": kind,
                "at": utc_now(),
                "payload": payload,
                "meta": meta or {},
                "prev": self.index.head,
            }
            record = {**body, "hash": _record_hash(body)}
            line = (canonical_json(record) + "\n").encode("utf-8")
            with self.log_path.open("ab") as fh:
                fh.write(line)
                fh.flush()
                os.fsync(fh.fileno())
            self._good_offset += len(line)
            self._log_size = self.log_path.stat().st_size
            _IndexBuilder.apply(self.index, record)
            return record

    def _load_if_changed(self) -> None:
        if self.log_path.stat().st_size != self._log_size:
            report = self._load()
            if not report.ok:
                raise StoreError(StoreErrorCode.STORE_CORRUPT, "Store log changed and failed verification.", details=report.to_document())

    # ------------------------------------------------------------------ privacy
    def _pseudonym_key(self) -> bytes:
        path = self.root / ".pseudonym.key"
        if not path.exists():
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(secrets.token_bytes(32))
        return path.read_bytes()

    def _privacy(self, doc: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        redacted, rules = self._redactor.redact(doc)
        if self.policy.pseudonymize_actor_ids and isinstance(redacted.get("actor"), dict):
            actor_id = str(redacted["actor"]["id"])
            if not actor_id.startswith("anon:"):
                mac = hmac.new(self._pseudonym_key(), actor_id.encode("utf-8"), hashlib.sha256).hexdigest()[:24]
                redacted["actor"] = {**redacted["actor"], "id": f"anon:{mac}"}
                rules = sorted({*rules, "pseudonymize_actor_id"})
        meta: dict[str, Any] = {"redacted": bool(rules), "redaction_rules": rules}
        if not rules:
            # Only fingerprint content that was stored unmodified; a hash of pre-redaction content could leak it.
            meta["content_sha256"] = sha256_of(doc)
        return redacted, meta

    # ------------------------------------------------------------------ ingest
    def ingest_receipt(self, receipt: Any, *, source: str = "ingest") -> dict[str, Any]:
        """Validate (Contract v1), redact, and append a receipt. Returns {status: stored|duplicate, receipt_id, ...}."""
        try:
            doc = check_envelope(receipt, "receipt")
        except BluxError as exc:
            raise StoreError(
                StoreErrorCode.INVALID_RECORD, "Not a valid blux.receipt.", details={"contract_error": exc.to_document()}
            ) from None
        stored_doc, meta = self._privacy(doc)
        stored_doc = check_envelope(stored_doc, "receipt")  # redaction must preserve validity
        meta["source"] = source
        rid, op = doc["receipt_id"], doc["operation_id"]

        def check() -> dict[str, Any] | None:
            existing = self.index.receipts.get(rid)
            if existing is not None:
                if existing["payload"] == stored_doc:
                    return {"status": "duplicate", "receipt_id": rid, "operation_id": op, "seq": existing["seq"]}
                raise StoreError(
                    StoreErrorCode.RECEIPT_CONFLICT, "receipt_id already stored with different content.", details={"receipt_id": rid}
                )
            other = self.index.by_operation.get(op)
            if other is not None:
                raise StoreError(
                    StoreErrorCode.RECEIPT_CONFLICT,
                    "operation_id already has a receipt.",
                    details={"operation_id": op, "receipt_id": other},
                )
            if self._cancellation(op) is not None:
                raise StoreError(
                    StoreErrorCode.ALREADY_FINISHED, "Operation was cancelled before this receipt.", details={"operation_id": op}
                )
            return None

        record = self._append("receipt", stored_doc, meta, check)
        if record.get("status") == "duplicate":
            return record
        return {"status": "stored", "receipt_id": rid, "operation_id": op, "seq": record["seq"], "redacted": meta["redacted"]}

    def ingest_artifact(self, artifact: Any, data: bytes | None = None) -> dict[str, Any]:
        try:
            doc = check_envelope(artifact, "artifact")
        except BluxError as exc:
            raise StoreError(
                StoreErrorCode.INVALID_RECORD, "Not a valid blux.artifact.", details={"contract_error": exc.to_document()}
            ) from None
        if data is not None:
            self._check_bytes(doc, data)
        stored_doc, meta = self._privacy(doc)
        stored_doc = check_envelope(stored_doc, "artifact")

        def check() -> dict[str, Any] | None:
            existing = self.index.artifacts.get(doc["artifact_id"])
            if existing is not None:
                if existing["payload"] == stored_doc:
                    return {"status": "duplicate", "artifact_id": doc["artifact_id"]}
                raise StoreError(
                    StoreErrorCode.RECEIPT_CONFLICT,
                    "artifact_id already stored with different content.",
                    details={"artifact_id": doc["artifact_id"]},
                )
            return None

        if data is not None:
            blob = self._blob_path(doc["sha256"])
            blob.parent.mkdir(parents=True, exist_ok=True)
            if not blob.exists():
                tmp = blob.with_suffix(".tmp")
                tmp.write_bytes(data)
                os.replace(tmp, blob)
            meta["blob"] = True
        record = self._append("artifact", stored_doc, meta, check)
        if record.get("status") == "duplicate":
            return record
        return {"status": "stored", "artifact_id": doc["artifact_id"], "seq": record["seq"]}

    def record_event(
        self,
        operation_id: str,
        event_type: str,
        data: dict[str, Any] | None = None,
        check: Callable[[], dict[str, Any] | None] | None = None,
    ) -> dict[str, Any]:
        if not ids.is_valid(ids.IdKind.OPERATION, operation_id):
            raise StoreError(StoreErrorCode.INVALID_RECORD, "Invalid operation_id.", details={"operation_id": operation_id[:64]})
        if event_type not in EVENT_TYPES:
            raise StoreError(
                StoreErrorCode.INVALID_RECORD, "Unknown event type.", details={"type": event_type, "allowed": list(EVENT_TYPES)}
            )
        payload, meta = self._privacy(
            {"event_id": new_event_id(), "operation_id": operation_id, "type": event_type, "data": dict(data or {})}
        )
        record = self._append("event", payload, meta, check)
        if record.get("status") == "duplicate":
            return record
        return {"status": "stored", "event_id": payload["event_id"], "seq": record["seq"], "redacted": meta["redacted"]}

    def cancel(
        self, operation_id: str, *, reason: str, parent_operation_id: str | None = None, session_id: str | None = None
    ) -> dict[str, Any]:
        """Record that an operation was cancelled. Only valid before its receipt exists."""
        for name, value, kind in (
            ("parent_operation_id", parent_operation_id, ids.IdKind.OPERATION),
            ("session_id", session_id, ids.IdKind.SESSION),
        ):
            if value is not None and not ids.is_valid(kind, value):
                raise StoreError(StoreErrorCode.INVALID_RECORD, f"Invalid {name}.", details={name: value[:64]})

        def check() -> dict[str, Any] | None:
            if operation_id in self.index.by_operation:
                raise StoreError(
                    StoreErrorCode.ALREADY_FINISHED, "Operation already has a receipt.", details={"operation_id": operation_id}
                )
            if self._cancellation(operation_id) is not None:
                return {"status": "duplicate", "operation_id": operation_id}
            return None

        return self.record_event(
            operation_id, "cancelled", {"reason": reason, "parent_operation_id": parent_operation_id, "session_id": session_id}, check
        )

    def _cancellation(self, operation_id: str) -> dict[str, Any] | None:
        for rec in self.index.events:
            if rec["payload"]["operation_id"] == operation_id and rec["payload"]["type"] == "cancelled":
                return rec
        return None

    # ------------------------------------------------------------------ artifacts
    def _blob_path(self, sha: str) -> Path:
        return self.root / "blobs" / "sha256" / sha[:2] / sha

    @staticmethod
    def _check_bytes(doc: dict[str, Any], data: bytes) -> None:
        actual = hashlib.sha256(data).hexdigest()
        if actual != doc["sha256"] or len(data) != doc["size_bytes"]:
            raise StoreError(
                StoreErrorCode.ARTIFACT_HASH_MISMATCH,
                "Artifact bytes do not match the descriptor.",
                details={
                    "artifact_id": doc["artifact_id"],
                    "expected_sha256": doc["sha256"],
                    "actual_sha256": actual,
                    "expected_size": doc["size_bytes"],
                    "actual_size": len(data),
                },
            )

    def verify_artifact(self, artifact_id: str, path: str | Path | None = None) -> dict[str, Any]:
        rec = self.index.artifacts.get(artifact_id)
        if rec is None:
            raise StoreError(StoreErrorCode.NOT_FOUND, "Unknown artifact.", details={"artifact_id": artifact_id})
        doc = rec["payload"]
        source = Path(path) if path is not None else self._blob_path(doc["sha256"])
        if not source.is_file():
            raise StoreError(
                StoreErrorCode.NOT_FOUND,
                "No bytes available to verify.",
                details={"artifact_id": artifact_id, "source": "file" if path else "blob"},
            )
        hasher, size = hashlib.sha256(), 0
        with source.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                hasher.update(chunk)
                size += len(chunk)
        ok = hasher.hexdigest() == doc["sha256"] and size == doc["size_bytes"]
        return {
            "artifact_id": artifact_id,
            "ok": ok,
            "expected_sha256": doc["sha256"],
            "actual_sha256": hasher.hexdigest(),
            "expected_size": doc["size_bytes"],
            "actual_size": size,
        }

    # ------------------------------------------------------------------ queries
    def get_receipt(self, receipt_id: str) -> dict[str, Any]:
        rec = self.index.receipts.get(receipt_id)
        if rec is None:
            raise StoreError(StoreErrorCode.NOT_FOUND, "Unknown receipt.", details={"receipt_id": receipt_id})
        return dict(rec["payload"])

    def receipt_for_operation(self, operation_id: str) -> dict[str, Any] | None:
        rid = self.index.by_operation.get(operation_id)
        return dict(self.index.receipts[rid]["payload"]) if rid else None

    def receipts(
        self,
        *,
        session_id: str | None = None,
        project_id: str | None = None,
        capability_id: str | None = None,
        status: str | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> list[dict[str, Any]]:
        try:
            lo = parse_timestamp(since) if since else None
            hi = parse_timestamp(until) if until else None
        except ValueError:
            raise StoreError(StoreErrorCode.INVALID_RECORD, "since/until must be RFC 3339 UTC timestamps (…Z).") from None
        out = []
        for rec in self.index.receipts.values():
            r = rec["payload"]
            if session_id is not None and r["session_id"] != session_id:
                continue
            if project_id is not None and r["project_id"] != project_id:
                continue
            if capability_id is not None and r["capability_id"] != capability_id:
                continue
            if status is not None and status_of_receipt(r).value != status:
                continue
            started = parse_timestamp(r["started_at"])
            if (lo and started < lo) or (hi and started > hi):
                continue
            out.append(dict(r))
        return sorted(out, key=lambda r: (r["started_at"], r["receipt_id"]))

    def events(self, operation_id: str | None = None) -> list[dict[str, Any]]:
        return [
            dict(e["payload"], at=e["at"])
            for e in self.index.events
            if operation_id is None or e["payload"]["operation_id"] == operation_id
        ]

    def artifacts(self, operation_id: str | None = None) -> list[dict[str, Any]]:
        return [
            dict(a["payload"])
            for a in self.index.artifacts.values()
            if operation_id is None or a["payload"]["operation_id"] == operation_id
        ]

    def replay_metadata(self, operation_id: str) -> dict[str, Any]:
        """What a replay needs: identity of the capability and the hashes a re-run must reproduce."""
        r = self.receipt_for_operation(operation_id)
        if r is None:
            raise StoreError(StoreErrorCode.NOT_FOUND, "No receipt for operation.", details={"operation_id": operation_id})
        return {
            "operation_id": operation_id,
            "capability_id": r["capability_id"],
            "capability_version": r["capability_version"],
            "contract_version": r["contract_version"],
            "input_sha256": r["input_sha256"],
            "output_sha256": r["output_sha256"],
            "issuer": r["issuer"],
            "replayable": r["status"] == "succeeded" and r["output_sha256"] is not None,
        }

    def verify_replay(self, operation_id: str, *, input_doc: Any = None, output_doc: Any = None) -> dict[str, Any]:
        meta = self.replay_metadata(operation_id)
        result: dict[str, Any] = {"operation_id": operation_id}
        if input_doc is not None:
            result["input_match"] = sha256_of(input_doc) == meta["input_sha256"]
        if output_doc is not None:
            result["output_match"] = meta["output_sha256"] is not None and sha256_of(output_doc) == meta["output_sha256"]
        result["ok"] = all(v for k, v in result.items() if k.endswith("_match"))
        return result

    # ------------------------------------------------------------------ export / import
    def export(self, out: BinaryIO) -> dict[str, Any]:
        header = {
            "format": "blux-system-export",
            "version": 1,
            "exported_at": utc_now(),
            "records": len(self.index.records),
            "head": self.index.head,
        }
        out.write((canonical_json(header) + "\n").encode("utf-8"))
        for rec in self.index.records:
            out.write((canonical_json(rec) + "\n").encode("utf-8"))
        return header

    def import_bundle(self, data: bytes) -> dict[str, Any]:
        lines = data.splitlines()
        if not lines:
            raise StoreError(StoreErrorCode.IMPORT_INVALID, "Empty export bundle.")
        try:
            header = loads_json(lines[0])
            records = [loads_json(line) for line in lines[1:]]
        except BluxError as exc:
            raise StoreError(
                StoreErrorCode.IMPORT_INVALID, "Bundle is not valid JSONL.", details={"contract_error": exc.to_document()}
            ) from None
        if header.get("format") != "blux-system-export" or header.get("version") != 1 or header.get("records") != len(records):
            raise StoreError(
                StoreErrorCode.IMPORT_INVALID,
                "Bad export header.",
                details={"header": {k: header.get(k) for k in ("format", "version", "records")}},
            )
        prev = GENESIS
        for i, rec in enumerate(records, 1):
            body = {k: rec.get(k) for k in ("seq", "kind", "at", "payload", "meta", "prev")}
            if rec.get("seq") != i or rec.get("prev") != prev or rec.get("hash") != _record_hash(body):
                raise StoreError(StoreErrorCode.IMPORT_INVALID, "Bundle hash chain is broken.", details={"seq": i})
            prev = rec["hash"]
        if prev != header.get("head"):
            raise StoreError(StoreErrorCode.IMPORT_INVALID, "Bundle head does not match header.")
        counts = {"stored": 0, "duplicate": 0, "skipped": 0}
        for rec in records:
            kind, payload = rec["kind"], rec["payload"]
            if kind == "receipt":
                res = self.ingest_receipt(payload, source="import")
            elif kind == "artifact":
                res = self.ingest_artifact(payload)
            elif kind == "event":
                if payload["type"] == "cancelled" and (
                    payload["operation_id"] in self.index.by_operation or self._cancellation(payload["operation_id"])
                ):
                    counts["duplicate"] += 1
                    continue
                if any(e["payload"]["event_id"] == payload["event_id"] for e in self.index.events):
                    counts["duplicate"] += 1
                    continue
                self._append("event", payload, {**rec.get("meta", {}), "source": "import"})
                res = {"status": "stored"}
            else:  # retention / recovery records describe the source store, not executions
                counts["skipped"] += 1
                continue
            counts[res["status"]] += 1
        return counts

    # ------------------------------------------------------------------ retention
    def apply_retention(self, *, now: datetime | None = None) -> dict[str, Any]:
        """Compact the log, dropping records older than the policy allows. The one sanctioned rewrite."""
        now = now or datetime.now(timezone.utc)
        cutoffs = {k: (now - timedelta(days=v)) for k, v in self.policy.keep_days.items() if v is not None}
        if not cutoffs:
            return {"removed": {}, "kept": len(self.index.records)}

        def expired(rec: dict[str, Any]) -> bool:
            kind = rec["kind"]
            if kind not in cutoffs:
                return False
            p = rec["payload"]
            when = p.get("finished_at") or p.get("created_at") or rec["at"]
            return parse_timestamp(when) < cutoffs[kind]

        with self._lock():
            self._load_if_changed()
            keep = [r for r in self.index.records if not expired(r)]
            removed: dict[str, int] = {}
            for r in self.index.records:
                if expired(r):
                    removed[r["kind"]] = removed.get(r["kind"], 0) + 1
            if not removed:
                return {"removed": {}, "kept": len(self.index.records)}
            fresh = Index()
            tmp = self.log_path.with_suffix(".compact")
            with tmp.open("wb") as fh:
                for r in keep:
                    body = {
                        "seq": len(fresh.records) + 1,
                        "kind": r["kind"],
                        "at": r["at"],
                        "payload": r["payload"],
                        "meta": r["meta"],
                        "prev": fresh.head,
                    }
                    new = {**body, "hash": _record_hash(body)}
                    fh.write((canonical_json(new) + "\n").encode("utf-8"))
                    _IndexBuilder.apply(fresh, new)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.log_path)
            self.index = fresh
            self._good_offset = self.log_path.stat().st_size
            self._log_size = self._good_offset
        cut = {k: v.isoformat().replace("+00:00", "Z") for k, v in cutoffs.items()}
        self._append("retention", {"removed": removed, "cutoffs": cut})
        self._prune_blobs()
        return {"removed": removed, "kept": len(keep)}

    def _prune_blobs(self) -> None:
        live = {a["payload"]["sha256"] for a in self.index.artifacts.values()}
        base = self.root / "blobs" / "sha256"
        if base.is_dir():
            for blob in base.glob("*/*"):
                if blob.name not in live:
                    blob.unlink()


class _IndexBuilder:
    """Reads log.jsonl, verifies the chain and builds the in-memory index."""

    def __init__(self, store: ReceiptStore) -> None:
        self.store = store
        self.index = Index()
        self.good_offset = 0

    def build(self) -> VerifyReport:
        problems: list[dict[str, Any]] = []
        torn = False
        lines = list(self.store._iter_lines())
        for n, (offset, raw, has_nl) in enumerate(lines, 1):
            last = n == len(lines)
            try:
                rec = json.loads(raw)
                body = {k: rec[k] for k in ("seq", "kind", "at", "payload", "meta", "prev")}
            except (json.JSONDecodeError, KeyError, TypeError, UnicodeDecodeError):
                if last and not has_nl:
                    torn = True
                    problems.append({"line": n, "reason": "torn_tail"})
                else:
                    problems.append({"line": n, "reason": "unparseable"})
                break
            reason = None
            if rec["seq"] != len(self.index.records) + 1:
                reason = "sequence_gap"
            elif rec["prev"] != self.index.head:
                reason = "broken_chain"
            elif rec.get("hash") != _record_hash(body):
                reason = "hash_mismatch"
            elif rec["kind"] not in KINDS:
                reason = "unknown_kind"
            elif not has_nl and not last:
                reason = "unparseable"
            if reason:
                problems.append({"line": n, "seq": rec.get("seq"), "reason": reason})
                break
            self.apply(self.index, rec)
            self.good_offset = offset + len(raw)
        return VerifyReport(ok=not problems, records=len(self.index.records), head=self.index.head, problems=problems, torn_tail=torn)

    @staticmethod
    def apply(index: Index, rec: dict[str, Any]) -> None:
        index.records.append(rec)
        index.head = rec["hash"]
        p = rec["payload"]
        if rec["kind"] == "receipt":
            index.receipts[p["receipt_id"]] = rec
            index.by_operation[p["operation_id"]] = p["receipt_id"]
        elif rec["kind"] == "artifact":
            index.artifacts[p["artifact_id"]] = rec
        elif rec["kind"] == "event":
            index.events.append(rec)
