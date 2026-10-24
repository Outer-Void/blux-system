"""Execution statuses recorded by BLUX System.

own status (one operation, from its receipt or a cancellation event):
    SUCCESS    receipt.status == "succeeded"
    FAILURE    receipt.status == "failed", or "rejected" for any reason other than policy
    DENIED     receipt.status == "rejected" with error_code POLICY_DENIED
    CANCELLED  a cancellation event was recorded before any receipt

aggregate status (an operation plus its descendants):
    PARTIAL    own SUCCESS but at least one descendant is not SUCCESS
    otherwise  the own status
"""

from __future__ import annotations

import enum
from collections.abc import Iterable
from typing import Any


class Status(str, enum.Enum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    PARTIAL = "PARTIAL"
    DENIED = "DENIED"
    CANCELLED = "CANCELLED"


def status_of_receipt(receipt: dict[str, Any]) -> Status:
    status = receipt["status"]
    if status == "succeeded":
        return Status.SUCCESS
    if status == "rejected" and receipt.get("error_code") == "POLICY_DENIED":
        return Status.DENIED
    if status in ("failed", "rejected"):
        return Status.FAILURE
    raise ValueError(f"unknown receipt status {status!r}")


def aggregate(own: Status | None, descendants: Iterable[Status | None]) -> Status | None:
    if own is None:
        return None
    if own is not Status.SUCCESS:
        return own
    return Status.PARTIAL if any(d is not Status.SUCCESS for d in descendants) else Status.SUCCESS
