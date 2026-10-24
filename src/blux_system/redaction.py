"""Receipt/event redaction applied before anything is persisted.

Two rule kinds:
- key rules: a mapping value under a sensitive key name (password, token, api_key, ...) is replaced whole;
- pattern rules: credential-shaped substrings (API keys, bearer tokens, private keys, ...) are replaced.

Structural contract fields (ids, hashes, timestamps, enums, versions) are never touched, so a redacted
receipt is still a valid BLUX Contract v1 document.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

PATTERNS: dict[str, str] = {
    "private_key": r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)",
    "anthropic_key": r"sk-ant-[A-Za-z0-9_-]{16,}",
    "openai_key": r"sk-(?:proj-)?[A-Za-z0-9_-]{20,}",
    "github_token": r"gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}",
    "aws_access_key": r"(?:AKIA|ASIA)[0-9A-Z]{16}",
    "slack_token": r"xox[baprs]-[A-Za-z0-9-]{10,}",
    "telegram_bot_token": r"\b[0-9]{8,10}:AA[A-Za-z0-9_-]{33}\b",
    "google_api_key": r"AIza[0-9A-Za-z_-]{35}",
    "stripe_key": r"\b(?:sk|rk)_(?:live|test)_[0-9A-Za-z]{16,}",
    "jwt": r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}",
    "bearer": r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{12,}",
    "url_credentials": r"(?<=://)[^/\s:@]+:[^/\s@]+(?=@)",
}

SENSITIVE_KEYS = (
    "password", "passwd", "secret", "token", "api_key", "apikey", "access_key", "private_key",
    "authorization", "cookie", "session_key", "credential", "client_secret",
)  # fmt: skip

# Contract fields that are structural and must never be rewritten.
PROTECTED_KEYS = frozenset(
    {
        "contract", "contract_version", "receipt_id", "operation_id", "parent_operation_id", "session_id",
        "project_id", "capability_id", "capability_version", "artifact_id", "artifact_ids", "status", "error_code",
        "effect", "input_sha256", "output_sha256", "sha256", "size_bytes", "media_type", "started_at", "finished_at",
        "created_at", "type", "version", "event_id", "at",
    }
)  # fmt: skip


@dataclass(frozen=True)
class Redactor:
    extra_key_names: tuple[str, ...] = ()
    extra_patterns: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        compiled = {name: re.compile(p) for name, p in {**PATTERNS, **self.extra_patterns}.items()}
        object.__setattr__(self, "_compiled", compiled)

    def _sensitive_key(self, key: str) -> bool:
        """Single-word names match whole key segments (token, not tokens_used); multi-word names match as substrings."""
        k = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key).lower().replace("-", "_")
        segments = set(k.split("_"))
        for name in SENSITIVE_KEYS + tuple(n.lower() for n in self.extra_key_names):
            if ("_" in name and name in k) or name in segments:
                return True
        return False

    def redact(self, value: Any) -> tuple[Any, list[str]]:
        """Return (redacted copy, sorted rule names that fired)."""
        fired: set[str] = set()
        out = self._walk(value, fired, key=None)
        return out, sorted(fired)

    def _walk(self, value: Any, fired: set[str], key: str | None) -> Any:
        if isinstance(value, dict):
            out: dict[str, Any] = {}
            for k, v in value.items():
                if k in PROTECTED_KEYS:
                    out[k] = v
                elif self._sensitive_key(k) and v not in (None, "", [], {}):
                    fired.add(f"key:{k.lower()}")
                    out[k] = "[REDACTED:key]"
                else:
                    out[k] = self._walk(v, fired, k)
            return out
        if isinstance(value, list):
            return [self._walk(v, fired, key) for v in value]
        if isinstance(value, str):
            for name, rx in self._compiled.items():  # type: ignore[attr-defined]
                value, n = rx.subn(f"[REDACTED:{name}]", value)
                if n:
                    fired.add(name)
            return value
        return value
