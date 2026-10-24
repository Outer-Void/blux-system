# Changelog

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
