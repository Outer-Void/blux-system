# Determinism

BLUX snapshots and receipts are deterministic by construction.

## Rules

1. Canonical JSON serialization (sorted keys, no extra whitespace).
2. Stable ordering of file records by path.
3. Stable hashing using SHA-256 over canonical JSON payloads.
4. Deterministic timestamps when `BLUX_DETERMINISTIC_TIMESTAMP` is set.

## Timestamp strategy

When the `BLUX_DETERMINISTIC_TIMESTAMP` environment variable is present, its
value is used for `created_at` fields. Otherwise, the current UTC timestamp is
recorded. This enables deterministic replay in tests and controlled runs.

## Hashing

Hashes are stored as `sha256:<hex>` and are computed over:

- File contents for input/output records.
- Canonical JSON payloads for snapshot/receipt hashes.
