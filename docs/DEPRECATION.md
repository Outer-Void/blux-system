# Deprecation Policy

This repository preserves deterministic, record-only contracts. Deprecations are
tracked as data schema evolution rather than behavior changes.

## Policy

- Deprecated fields remain readable for at least one major contract version.
- New fields are added as optional unless a new major contract version is
  published.
- Replay and validation tooling must continue to accept prior receipts and
  snapshots by treating unknown fields as invalid (schema enforcement) and
  missing optional fields as acceptable.

## Process

1. Mark the field as deprecated in documentation and release notes.
2. Keep the field in schemas with a clear note until the next major version.
3. Add compatibility guidance in `docs/COMPATIBILITY.md`.
4. Only remove deprecated fields in a major version update.

## Stage 02 (2026-10-24): legacy snapshot/receipt records

The pre-Contract-v1 records (`snapshot`, legacy `execution_receipt`, `replay_report`, `system_state`) and the
`snapshot`, `receipt` and `replay` CLI commands are **deprecated** in favour of Contract v1 `blux.receipt`
documents stored by `ReceiptStore` (see `docs/STORE.md`).

- They remain readable and produce identical output through at least the next major version.
- Code moved to `blux_system.legacy.core`. `blux_system.core` and the package root re-export it.
- Their schemas now ship inside the package (`blux_system/legacy/schemas/`). Previously they resolved relative
  to the source tree, which broke installed wheels (Stage 00 finding).
- `blux-app` reads these files by name and is unaffected.
