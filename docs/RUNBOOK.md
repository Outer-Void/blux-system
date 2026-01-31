# Runbook

This runbook describes deterministic recordkeeping workflows for BLUX System
State v1.0. All commands are record-only; no autonomous behavior is executed.

## Prerequisites

Install Python 3 using the platform instructions in `docs/PLATFORMS.md`.

Create a virtual environment (optional):

```sh
python3 -m venv .venv
. .venv/bin/activate
```

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

## Snapshot

```sh
blux-system snapshot --in <input_dir> --out <dir>
```

The snapshot is written as `<dir>/snapshot.json`.

## Receipt

```sh
blux-system receipt --snapshot <snapshot.json> --out <dir>
```

The receipt is written as `<dir>/receipt.json`.

## Replay verification

```sh
blux-system replay --receipt <receipt.json> --root <output_dir>
```

The replay report is written as `<output_dir>/replay_report.json`.

## Deterministic runs

To force deterministic timestamps in generated JSON:

```sh
export BLUX_DETERMINISTIC_TIMESTAMP="2024-01-01T00:00:00Z"
```

Windows PowerShell:

```powershell
$env:BLUX_DETERMINISTIC_TIMESTAMP = "2024-01-01T00:00:00Z"
```
