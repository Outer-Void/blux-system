# Orchestrator Concepts (Record-Only)

This document defines orchestrator-related metadata as data contracts only.
There is no autonomy, no decision-making, and no control logic implemented.

## Routing metadata

Routing metadata is recorded to explain which agent handled a step. It is
represented in receipts as:

- `run_graph.steps[].agent`
- `agent.requested_model_version` / `agent.resolved_model_version`

These fields describe what happened; they do not trigger any routing.

## Layer identifiers

Layer identifiers are explicit strings used to label execution steps. They are
captured in:

- `run_graph.steps[].id`
- `run_graph.steps[].input_ref`
- `run_graph.steps[].output_ref`

Identifiers must be deterministic and stable across replays.

## Sequencing model

The sequencing model is a static, record-only list of steps ordered by `id`.
Receipts normalize steps to a deterministic ordering; no dynamic sequencing is
performed.

## No autonomy

- No routing decisions are made at runtime.
- No automated coordination or scheduling is performed.
- Orchestrator concepts exist solely as metadata for verification and replay.
