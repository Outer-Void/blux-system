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
