# blux-system

BLUX system contracts, deterministic snapshots, and execution receipts.
This repository **records state and receipts only** and intentionally performs
no intelligence, orchestration, or decision-making.

## What is included

- JSON schemas for system state, snapshots, and receipts.
- Deterministic snapshot + receipt builders.
- Replay verification reports.
- Minimal CLI for recording snapshots/receipts and replay reports.

## What is *not* included

- No orchestration of tools or agents.
- No inference logic or policy engines.
- No mutation of inputs/outputs beyond hashing and recording.

## Quick start

```sh
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

Record a snapshot:

```sh
blux-system snapshot --in ./inputs --out ./recordings
```

Record a receipt from an existing snapshot:

```sh
blux-system receipt --snapshot ./recordings/snapshot.json --out ./recordings
```

## Documentation

- [Contracts](docs/CONTRACT.md)
- [Determinism](docs/DETERMINISM.md)
- [Replay](docs/REPLAY.md)
- [Runbook](docs/RUNBOOK.md)
- [Compatibility](docs/COMPATIBILITY.md)
- [Orchestrator Concepts](docs/ORCHESTRATOR_CONCEPTS.md)
- [Platforms](docs/PLATFORMS.md)
