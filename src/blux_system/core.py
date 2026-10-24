"""Compatibility shim: legacy APIs moved to blux_system.legacy.core (deprecated)."""

from blux_system.legacy.core import *  # noqa: F403
from blux_system.legacy.core import (  # noqa: F401
    _hash_file,
    _validate_receipt_hash,
    build_receipt_from_snapshot,
    build_replay_report,
    build_snapshot_from_dirs,
    canonical_json_bytes,
    load_state,
    make_receipt,
    make_snapshot,
    save_state,
)
