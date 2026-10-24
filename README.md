# blux-system

**BLUX System is the non-intelligent execution truth layer of BLUX.** It records what happened. It does not
decide, reason or orchestrate.

| Owns | Does not own |
|---|---|
| receipts (BLUX Contract v1 `blux.receipt`) | reasoning, planning, model calls |
| operation graph (parent → child) | autonomous orchestration / scheduling |
| artifact references and hash verification | policy decisions (BLUX runtime) |
| event timeline (incl. cancellations) | capability execution |
| replay metadata | |
| query and inspection surfaces | |
| retention and privacy metadata | |

Contract v1 comes from [`blux-sdk`](https://github.com/Outer-Void/blux/tree/main/sdk/python), pinned to an
immutable commit in `pyproject.toml`. Schemas are never copied into this repository.

## Statuses
| Status | Meaning |
|---|---|
| `SUCCESS` | receipt `succeeded` |
| `FAILURE` | receipt `failed`, or `rejected` for a reason other than policy |
| `DENIED` | receipt `rejected` with `POLICY_DENIED` |
| `CANCELLED` | cancellation recorded before any receipt |
| `PARTIAL` | aggregate only: the operation succeeded but something in its subtree did not |

## Quick start
```sh
pip install .            # pulls blux-sdk from Outer-Void/blux at the pinned commit
blux-system init --store ./truth
blux run echo.test --input '{"message":"hi"}' --system-store ./truth   # from the blux repo
blux-system graph --store ./truth
blux-system timeline --store ./truth
blux-system verify --store ./truth
```

All store commands print exactly one JSON document to stdout. Exit codes are 0 ok, 1 failure (with an error
document), 2 usage.

| Command | Purpose |
|---|---|
| `init`, `ingest`, `cancel`, `note` | write |
| `show`, `list`, `graph`, `timeline`, `replay-check`, `artifact-verify` | inspect |
| `verify`, `export`, `import`, `retention` | maintain |

## Python API
```python
from blux_system import ReceiptStore
from blux_system.graph import build_graph, timeline

store = ReceiptStore.open("./truth")
store.ingest_receipt(receipt_doc)  # validates via blux-sdk, redacts, appends
graph = build_graph(store, session_id=...)
```

See [docs/STORE.md](docs/STORE.md) for the storage format, immutability, corruption handling, redaction and
retention.

## Legacy (deprecated)
The pre-Contract-v1 snapshot/receipt/replay tooling (`blux-system snapshot|receipt|replay`,
`blux_system.legacy`) still works unchanged and now ships its schemas inside the package. See
[docs/DEPRECATION.md](docs/DEPRECATION.md).

Licence: BLUX Proprietary (see `LICENSE`). Third-party credits: [Outer-Void/credits](https://github.com/Outer-Void/credits).
