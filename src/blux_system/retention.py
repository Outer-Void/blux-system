"""Retention and privacy policy for a store (`<store>/retention.json`)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from blux_system.errors import StoreError, StoreErrorCode
from blux_system.redaction import Redactor

KINDS = ("receipt", "event", "artifact")

DEFAULT_POLICY: dict[str, Any] = {
    "contract": "blux-system.retention",
    "version": 1,
    "keep_days": {"receipt": None, "event": None, "artifact": None},
    "pseudonymize_actor_ids": False,
    "redact": {"extra_key_names": [], "extra_patterns": {}},
}


@dataclass(frozen=True)
class RetentionPolicy:
    keep_days: dict[str, int | None] = field(default_factory=lambda: dict.fromkeys(KINDS))
    pseudonymize_actor_ids: bool = False
    extra_key_names: tuple[str, ...] = ()
    extra_patterns: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_document(cls, doc: Any) -> RetentionPolicy:
        def bad(msg: str) -> StoreError:
            return StoreError(StoreErrorCode.INVALID_POLICY, msg)

        if not isinstance(doc, dict) or doc.get("contract") != "blux-system.retention" or doc.get("version") != 1:
            raise bad("retention policy must be a blux-system.retention version 1 document")
        unknown = set(doc) - set(DEFAULT_POLICY)
        if unknown:
            raise bad(f"unknown retention fields: {sorted(unknown)}")
        keep = doc.get("keep_days", {})
        if not isinstance(keep, dict) or set(keep) - set(KINDS):
            raise bad(f"keep_days keys must be among {list(KINDS)}")
        for k, v in keep.items():
            if v is not None and (not isinstance(v, int) or isinstance(v, bool) or v < 1):
                raise bad(f"keep_days.{k} must be null (forever) or a positive integer")
        redact = doc.get("redact", {})
        names = redact.get("extra_key_names", [])
        pats = redact.get("extra_patterns", {})
        if not (isinstance(names, list) and all(isinstance(n, str) and n for n in names)):
            raise bad("redact.extra_key_names must be a list of strings")
        if not isinstance(pats, dict):
            raise bad("redact.extra_patterns must map rule names to regular expressions")
        for name, pattern in pats.items():
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,40}", str(name)):
                raise bad(f"invalid redaction rule name {name!r}")
            try:
                re.compile(pattern)
            except (re.error, TypeError) as exc:
                raise bad(f"invalid redaction pattern {name!r}: {exc}") from None
        if not isinstance(doc.get("pseudonymize_actor_ids", False), bool):
            raise bad("pseudonymize_actor_ids must be a boolean")
        return cls(
            keep_days={k: keep.get(k) for k in KINDS},
            pseudonymize_actor_ids=doc.get("pseudonymize_actor_ids", False),
            extra_key_names=tuple(names),
            extra_patterns=dict(pats),
        )

    def to_document(self) -> dict[str, Any]:
        return {
            "contract": "blux-system.retention",
            "version": 1,
            "keep_days": dict(self.keep_days),
            "pseudonymize_actor_ids": self.pseudonymize_actor_ids,
            "redact": {"extra_key_names": list(self.extra_key_names), "extra_patterns": dict(self.extra_patterns)},
        }

    def redactor(self) -> Redactor:
        return Redactor(extra_key_names=self.extra_key_names, extra_patterns=self.extra_patterns)
