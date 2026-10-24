"""BLUX System: the non-intelligent execution truth layer.

Owns receipts, the operation graph, artifact references, the event timeline, replay metadata, query/inspection
and retention/privacy metadata. Performs no reasoning and no orchestration.

Contract v1 documents are validated through `blux-sdk` (Outer-Void/blux); schemas are never copied here.
The pre-Contract-v1 snapshot/receipt API is still importable from this package (deprecated; see
`blux_system.legacy`).
"""

__version__ = "2.0.0"

from blux_system.legacy.core import (
    build_replay_report,
    canonical_json_bytes,
    load_state,
    make_receipt,
    make_snapshot,
    save_state,
)
from blux_system.statuses import Status
from blux_system.store import ReceiptStore

__all__ = [
    "ReceiptStore",
    "Status",
    "__version__",
    "build_replay_report",
    "canonical_json_bytes",
    "load_state",
    "make_receipt",
    "make_snapshot",
    "save_state",
]
