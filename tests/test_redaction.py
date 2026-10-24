"""Secrets never reach disk; redaction keeps receipts valid Contract v1 documents."""

from __future__ import annotations

import stat
from pathlib import Path

from blux_sdk import ids
from blux_sdk.schemas import check_envelope

from blux_system.redaction import Redactor
from blux_system.store import ReceiptStore
from tests.conftest import receipt

SECRETS = {
    "openai": "sk-proj-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4",
    "anthropic": "sk-ant-api03-" + "ZYXWVUTSRQPONMLKJIHG",
    "github": "ghp_" + "a" * 36,
    "aws": "AKIA" + "ABCDEFGHIJKLMNOP",
    "telegram": "123456789:AA" + "b" * 33,
    "bearer": "Bearer abcdefghijklmnopqrstu.vwx",
    "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJlLXZhbHVl",
    "pem": "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASC\n-----END PRIVATE KEY-----",
    "url": "https://admin:hunter2pass@db.example.com/x",
}


def test_receipt_free_text_is_redacted_and_still_valid(store: ReceiptStore) -> None:
    r = receipt(reason=f"denied; token was {SECRETS['openai']} and {SECRETS['bearer']}", actor={"type": "user", "id": SECRETS["github"]})
    res = store.ingest_receipt(r)
    assert res["redacted"] is True
    stored = store.get_receipt(r["receipt_id"])
    check_envelope(stored, "receipt")
    assert SECRETS["openai"] not in str(stored) and SECRETS["github"] not in str(stored)
    assert "[REDACTED:openai_key]" in stored["policy_decision"]["reason"]
    assert stored["actor"]["id"] == "[REDACTED:github_token]"
    # structural fields untouched
    for k in ("receipt_id", "operation_id", "input_sha256", "output_sha256", "started_at", "status"):
        assert stored[k] == r[k]
    meta = store.index.receipts[r["receipt_id"]]["meta"]
    assert meta["redaction_rules"] == ["bearer", "github_token", "openai_key"]
    assert "content_sha256" not in meta  # no fingerprint of pre-redaction content


def test_unredacted_receipt_keeps_content_fingerprint(store: ReceiptStore) -> None:
    from blux_sdk.serialization import sha256_of

    r = receipt()
    assert store.ingest_receipt(r)["redacted"] is False
    assert store.index.receipts[r["receipt_id"]]["meta"]["content_sha256"] == sha256_of(r)


def test_event_data_keys_and_patterns(store: ReceiptStore) -> None:
    op = ids.new_operation_id()
    store.record_event(
        op,
        "note",
        {
            "api_key": "plain-but-sensitive",
            "Authorization": "x",
            "tokens_used": 12,
            "max_output_tokens": 50,
            "msg": " ".join(SECRETS.values()),
        },
    )
    data = store.events(op)[0]["data"]
    assert data["api_key"] == "[REDACTED:key]" and data["Authorization"] == "[REDACTED:key]"
    assert data["tokens_used"] == 12 and data["max_output_tokens"] == 50
    for s in SECRETS.values():
        assert s not in data["msg"]


def test_no_secret_bytes_on_disk(store: ReceiptStore) -> None:
    store.ingest_receipt(receipt(reason=" ".join(SECRETS.values())))
    store.record_event(ids.new_operation_id(), "note", {"password": "correct horse battery staple", "msg": SECRETS["aws"]})
    exported = store.root / "export.jsonl"
    with exported.open("wb") as fh:
        store.export(fh)
    for path in (store.log_path, exported):
        blob = path.read_bytes()
        for s in [*SECRETS.values(), "correct horse battery staple", "hunter2pass"]:
            assert s.encode() not in blob, (path.name, s[:12])


def test_policy_extra_rules_and_pseudonymization(tmp_path: Path) -> None:
    policy = {
        "contract": "blux-system.retention",
        "version": 1,
        "keep_days": {"receipt": None, "event": None, "artifact": None},
        "pseudonymize_actor_ids": True,
        "redact": {"extra_key_names": ["ssn"], "extra_patterns": {"ticket": "TICKET-[0-9]{4}"}},
    }
    store = ReceiptStore.init(tmp_path / "s", policy=policy)
    r1 = receipt(actor={"type": "user", "id": "alice@example.com"}, reason="see TICKET-1234")
    r2 = receipt(actor={"type": "user", "id": "alice@example.com"})
    store.ingest_receipt(r1)
    store.ingest_receipt(r2)
    s1, s2 = store.get_receipt(r1["receipt_id"]), store.get_receipt(r2["receipt_id"])
    assert s1["actor"]["id"].startswith("anon:") and s1["actor"]["id"] == s2["actor"]["id"]  # stable pseudonym
    assert "alice" not in store.log_path.read_text()
    assert "[REDACTED:ticket]" in s1["policy_decision"]["reason"]
    key = store.root / ".pseudonym.key"
    assert stat.S_IMODE(key.stat().st_mode) == 0o600
    store.record_event(ids.new_operation_id(), "note", {"ssn": "123-45-6789"})
    assert "123-45-6789" not in store.log_path.read_text()


def test_redactor_is_pure() -> None:
    doc = {"a": {"secret": "x", "list": ["Bearer abcdefghijklmnop"]}, "contract": "blux.receipt"}
    out, fired = Redactor().redact(doc)
    assert doc["a"]["secret"] == "x"  # input untouched
    assert out["a"]["secret"] == "[REDACTED:key]" and fired == ["bearer", "key:secret"]
