from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from blux_sdk import envelopes, ids
from blux_sdk.serialization import sha256_of

from blux_system.store import ReceiptStore

T0 = "2026-10-24T00:00:00.000Z"


def ts(seconds: float) -> str:
    whole, ms = int(seconds), round((seconds - int(seconds)) * 1000)
    return f"2026-10-24T00:{whole // 60:02d}:{whole % 60:02d}.{ms:03d}Z"


def receipt(
    *,
    operation_id: str | None = None,
    parent: str | None = None,
    session_id: str | None = None,
    status: str = "succeeded",
    error_code: str | None = None,
    capability_id: str = "echo.test",
    start: float = 0,
    end: float = 1,
    actor: dict[str, str] | None = None,
    reason: str = "allowed by rule",
    output: dict[str, Any] | None = None,
    artifact_ids: list[str] | None = None,
) -> dict[str, Any]:
    if status != "succeeded" and error_code is None:
        error_code = "EXECUTION_FAILED" if status == "failed" else "MALFORMED_INPUT"
    effect = "deny" if error_code == "POLICY_DENIED" else ("allow" if status != "rejected" else "not_evaluated")
    return envelopes.receipt(
        receipt_id=ids.new_receipt_id(),
        operation_id=operation_id or ids.new_operation_id(),
        parent_operation_id=parent,
        session_id=session_id,
        project_id="blux",
        capability_id=capability_id,
        capability_version="1.0.0",
        actor=actor or {"type": "user", "id": "tester"},
        status=status,
        error_code=error_code,
        policy_decision={"effect": effect, "policy_id": "blux.reference", "rule_id": None, "reason": reason},
        input_sha256=sha256_of({"message": "hi"}),
        output_sha256=sha256_of(output if output is not None else {"message": "hi"}) if status == "succeeded" else None,
        artifact_ids=artifact_ids or [],
        started_at=ts(start),
        finished_at=ts(end),
        issuer={"component": "blux-runtime", "version": "0.1.0"},
    )


@pytest.fixture
def store(tmp_path: Path) -> ReceiptStore:
    return ReceiptStore.init(tmp_path / "store")
