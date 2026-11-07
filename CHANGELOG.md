# Changelog

## Unreleased: OV-52
Found by the OV-52 review of BLUX System as the evidence authority (crashes, tampering, partial failure,
retention, migrations). Each fix has a test that fails on f7f8ede (`tests/test_evidence_authority.py`). The
format version stays 1: `head.json` is an addition that older stores gain at their next write.
- **Fix: a crash at the wrong byte could brick the store.** A record written without its newline made the next
  append run into it, after which every open was `STORE_CORRUPT`. The newline is now added and recorded
  (`unterminated_record`); a long-lived writer also repairs a torn tail another process left.
- **Fix: a shortened log verified as ok.** Records cut from the end, or an older copy of the log, were not
  detected. `head.json` anchors the last record (`truncated`, `anchor_mismatch`); `verify --expect-head` checks
  against a head kept elsewhere.
- **Fix: corruption crashed readers.** A record with recomputed hashes but a malformed payload, an unknown
  receipt status, a damaged `retention.json` or a non-object `store.json` raised tracebacks. They are now
  `STORE_CORRUPT` (`invalid_payload`, `details.reason`); a newer store format is `unsupported_version`.
  `verify` also checks payloads against Contract v1 and checks artifact blobs (`blob_mismatch`, `blob_missing`).
- **Fix: retention could remove records without a record of it.** A run killed after its rewrite left the
  removal unrecorded. The `retention` record (now with `previous_head` and `previous_records`) is written inside
  the new chain, installed with one rename.
- **Fix: import could stop part-way.** A conflict in the middle of a bundle left the first part imported, and
  malformed events (no type, invalid operation id) were imported. Everything is now checked first; a failing
  import imports nothing.
- **Fix: simultaneous writers were refused.** 15 of 16 simultaneous writers got `STORE_LOCKED` (BLUX then
  spooled their receipts). Writers now wait up to 10 s for the lock.
- Blobs are written under the writer lock (retention can no longer prune one before its record exists), fsynced,
  and repaired when an artifact is re-ingested with its bytes.
- Docs: `docs/STORE.md` no longer claims a re-hashed forgery is detected, and says what the chain proves.

## 2.0.0 (Stage 02)
- **Execution truth store** (`ReceiptStore`):
  - local-first, append-only, hash-chained log of Contract v1 receipts, artifacts and events;
  - immutable receipts with idempotent re-ingest and conflict detection;
  - writer locking, torn-tail recovery, explicit corruption errors.
- Operation graph reconstruction with statuses SUCCESS / FAILURE / PARTIAL / DENIED / CANCELLED, missing-parent placeholders and cycle reporting.
- Event timeline, replay metadata and verification, and artifact hash verification with an optional blob store.
- Redaction before persistence; retention/privacy policy (`keep_days`, actor pseudonymization, extra rules).
- Verifiable export/import bundles.
- `blux-system` store CLI (one JSON document per invocation).
- Contract v1 via `blux-sdk` pinned to Outer-Void/blux `0b09a46`. No schema copies.
- Legacy tooling moved to `blux_system.legacy` (deprecated); schemas packaged (fixes installed-wheel failures).

## 1.0.0
- Deterministic snapshots, legacy receipts and replay reports.
