# System Contracts (v1.0)

BLUX contracts define deterministic, non-intelligent records of system state,
snapshots, and execution receipts.

## System state

System state captures identifiers, timestamps, references to inputs/outputs, and
agent run headers.

Required fields:

- `session_id`, `project_id`
- `timestamps.created_at`, `timestamps.updated_at`
- `inputs[]`, `outputs[]` references (`path`, `hash`, optional `size`)
- `agent.input_hash`, `agent.model_version`, `agent.contract_version`

Schema: `schemas/system_state.schema.json`.

## Snapshot

Snapshots record deterministic metadata for input/output artifacts.

Required fields:

- `contract_version`
- `created_at`
- `inputs[]`, `outputs[]` (`path`, `hash`, `size`)
- `output_bundles[]` (`bundle_id`, `files[]`)
- `patch_bundles[]` (`bundle_id`, `base_path`, `patches[]`, `outputs[]`)
- `snapshot_hash` (sha256 of canonical snapshot payload)

Schema: `schemas/snapshot.schema.json`.

## Execution receipt

Receipts wrap a snapshot hash without altering referenced inputs/outputs.

Required fields:

- `contract_version`
- `created_at`
- `agent.input_hash`, `agent.model_version`, `agent.contract_version`
- `agent.requested_model_version`, `agent.resolved_model_version`, `agent.schema_version`
- `snapshot.hash`, `snapshot.contract_version`
- `snapshot_hash`
- `output_hashes[]` (`path`, `hash`, `size`)
- `ordering.outputs`, `ordering.output_bundles`, `ordering.patch_bundles`, `ordering.output_hashes`
- `ordering.run_graph_steps`
- Optional `policy_pack`, `reasoning_pack` identifiers
- Optional `run_graph.steps[]` (`id`, `agent`, `input_ref`, `output_ref`, `status`)
- Optional `dataset_fixture` (`id`, optional `hash`, optional `path`)
- `receipt_hash` (sha256 of canonical receipt payload)

Schema: `schemas/execution_receipt.schema.json`.
