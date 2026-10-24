"""Store-level errors. Contract-level problems inside a record keep their BLUX Contract v1 code in details."""

from __future__ import annotations

import enum
from typing import Any


class StoreErrorCode(str, enum.Enum):
    STORE_NOT_FOUND = "STORE_NOT_FOUND"
    STORE_EXISTS = "STORE_EXISTS"
    STORE_CORRUPT = "STORE_CORRUPT"
    STORE_LOCKED = "STORE_LOCKED"
    STORE_READ_ONLY = "STORE_READ_ONLY"
    INVALID_RECORD = "INVALID_RECORD"
    RECEIPT_CONFLICT = "RECEIPT_CONFLICT"
    ALREADY_FINISHED = "ALREADY_FINISHED"
    NOT_FOUND = "NOT_FOUND"
    ARTIFACT_HASH_MISMATCH = "ARTIFACT_HASH_MISMATCH"
    IMPORT_INVALID = "IMPORT_INVALID"
    INVALID_POLICY = "INVALID_POLICY"


class StoreError(Exception):
    def __init__(self, code: StoreErrorCode, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details: dict[str, Any] = dict(details or {})

    def to_document(self) -> dict[str, Any]:
        return {"contract": "blux-system.error", "version": 1, "code": self.code.value, "message": self.message, "details": self.details}

    def __repr__(self) -> str:
        return f"StoreError({self.code.value}, {self.message!r})"
