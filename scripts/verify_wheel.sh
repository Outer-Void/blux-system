#!/usr/bin/env bash
# Build the wheel, install it (with its pinned blux-sdk) into a fresh venv, and exercise the store from /tmp.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON:-python3}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
"$PYTHON" -m build --wheel --outdir "$WORK/dist" "$ROOT" >/dev/null
"$PYTHON" -m venv "$WORK/venv"
"$WORK/venv/bin/pip" install -q ${BLUX_SDK_SOURCE:+"$BLUX_SDK_SOURCE"} "$WORK"/dist/*.whl
cd "$WORK"
"$WORK/venv/bin/blux-system" init --store "$WORK/s" >/dev/null
"$WORK/venv/bin/python" - "$WORK/s" <<'PY'
import sys
from blux_sdk import envelopes, ids
from blux_system import ReceiptStore
from blux_system.graph import build_graph
from blux_system.legacy.core import _load_schema
store = ReceiptStore.open(sys.argv[1])
op = ids.new_operation_id()
r = envelopes.receipt(receipt_id=ids.new_receipt_id(), operation_id=op, parent_operation_id=None, session_id=None, project_id=None,
    capability_id="echo.test", capability_version="1.0.0", actor={"type": "user", "id": "u"}, status="succeeded", error_code=None,
    policy_decision={"effect": "allow", "policy_id": "p", "rule_id": "r", "reason": "x"}, input_sha256="0" * 64, output_sha256="0" * 64,
    artifact_ids=[], started_at="2026-10-24T00:00:00.000Z", finished_at="2026-10-24T00:00:01.000Z", issuer={"component": "c", "version": "0.1.0"})
assert store.ingest_receipt(r)["status"] == "stored"
assert build_graph(store)["nodes"][op]["aggregate_status"] == "SUCCESS"
assert _load_schema("execution_receipt.schema.json")
import blux_system; assert "site-packages" in blux_system.__file__
print("verify_wheel: OK", file=sys.stderr)
PY
