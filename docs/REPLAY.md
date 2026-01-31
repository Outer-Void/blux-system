# Replay

The replay command validates Phase 3/4 receipts against a filesystem root. It
performs no orchestration: it only verifies existence, hashes, and schema
conformance.

## Command

```sh
blux-system replay --receipt <file> --root <dir>
```

The command writes `replay_report.json` into the `--root` directory.

## Replay report

The report is deterministic and includes:

- Receipt schema validation results.
- Receipt hash verification.
- Per-output existence and hash checks.
- A summary with totals and an overall `ok` status.

Determinism is enforced via canonical JSON and optional
`BLUX_DETERMINISTIC_TIMESTAMP` overrides.
