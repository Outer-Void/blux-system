# Compatibility Guide

BLUX receipts and snapshots are deterministic records that evolve by extending
schemas with optional fields. This document describes how to read receipts
across versions.

## Reading older receipts

- Missing fields are treated as absent; do not assume defaults beyond
  deterministic ordering and hashing rules.
- `ordering` is authoritative for stable sort keys. If a newer `ordering`
  attribute is missing, fall back to the canonical order used in the version
  that produced the receipt.
- `run_graph`, `policy_pack`, `reasoning_pack`, and `dataset_fixture` are
  optional in v1.0 and may be absent in v0.x receipts.

## Version negotiation fields

Receipts store both requested and resolved model versions to explain which
runtime was used:

- `agent.requested_model_version`
- `agent.resolved_model_version`
- `agent.contract_version`
- `agent.schema_version`

Older receipts may only include `agent.model_version`. Treat it as the resolved
version when newer fields are missing.

## Schema evolution

- Schema validation is performed against the current JSON schemas.
- Older receipts remain valid when they satisfy required fields and omit newer
  optional fields.
- `contract_version` and `schema_version` are informational; compatibility is
  based on required fields and deterministic hashing.

## Replay compatibility

Replay validation does not make decisions or select data; it verifies the
presence and hashes of referenced outputs (and fixture files when a path is
recorded). If a fixture is referenced without a path, the report will record
`dataset_fixture_result` with no verification.
