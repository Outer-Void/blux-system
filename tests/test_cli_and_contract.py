from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import tomllib
from blux_sdk import ids

from tests.conftest import receipt

ROOT = Path(__file__).resolve().parent.parent


def run(*args: str, stdin: bytes | None = None, env: dict[str, str] | None = None) -> tuple[int, Any, str]:
    proc = subprocess.run([sys.executable, "-m", "blux_system.cli", *args], input=stdin, capture_output=True, timeout=60, env=env)
    out = proc.stdout.decode()
    doc = None
    if out:
        assert out.endswith("\n") and out.count("\n") == 1, out
        doc = json.loads(out)
    return proc.returncode, doc, proc.stderr.decode()


def test_cli_end_to_end(tmp_path: Path) -> None:
    store = str(tmp_path / "s")
    assert run("init", "--store", store)[0] == 0
    root = ids.new_operation_id()
    r1 = receipt(operation_id=root)
    r2 = receipt(parent=root, status="rejected", error_code="POLICY_DENIED")
    for r in (r1, r2):
        code, doc, _ = run("ingest", "--store", store, "-", stdin=json.dumps(r).encode())
        assert (code, doc["status"]) == (0, "stored")
    code, doc, _ = run("ingest", "--store", store, "-", stdin=json.dumps(r1).encode())
    assert doc["status"] == "duplicate"
    code, doc, _ = run("graph", "--store", store, "--root", root)
    assert code == 0 and doc["nodes"][root]["aggregate_status"] == "PARTIAL"
    assert doc["nodes"][r2["operation_id"]]["own_status"] == "DENIED"
    assert run("show", "--store", store, "--operation", root)[1] == r1
    assert run("list", "--store", store, "--status", "DENIED")[1]["count"] == 1
    assert run("timeline", "--store", store, "--operation", root)[1]["count"] == 4
    assert run("verify", "--store", store)[1]["ok"] is True
    code, doc, _ = run("cancel", "--store", store, r1["operation_id"], "--reason", "late")
    assert code == 1 and doc["code"] == "ALREADY_FINISHED"
    bundle = str(tmp_path / "b.jsonl")
    assert run("export", "--store", store, bundle)[1]["records"] == 2
    other = str(tmp_path / "o")
    run("init", "--store", other)
    assert run("import", "--store", other, bundle)[1] == {"stored": 2, "duplicate": 0, "skipped": 0}
    meta = run("replay-check", "--store", store, root)[1]
    assert meta["replayable"] is True
    v = run("version")[1]
    assert v["statuses"] == ["SUCCESS", "FAILURE", "PARTIAL", "DENIED", "CANCELLED"] and "1.0" in v["supported_contract_versions"]


def test_cli_failures_are_documents(tmp_path: Path) -> None:
    store = str(tmp_path / "s")
    code, doc, _ = run("list", "--store", store)
    assert (code, doc["code"]) == (1, "STORE_NOT_FOUND")
    run("init", "--store", store)
    code, doc, _ = run("ingest", "--store", store, "-", stdin=b"not json")
    assert (code, doc["code"]) == (1, "INVALID_RECORD")
    bad = receipt()
    bad["contract_version"] = "9.0"
    code, doc, _ = run("ingest", "--store", store, "-", stdin=json.dumps(bad).encode())
    assert doc["details"]["contract_error"]["code"] == "UNSUPPORTED_CONTRACT_VERSION"
    proc = subprocess.run([sys.executable, "-m", "blux_system.cli", "frobnicate"], capture_output=True)
    assert proc.returncode == 2 and proc.stdout == b""
    with open(Path(store) / "log.jsonl", "ab") as fh:
        fh.write(b"garbage\n")
    code, doc, _ = run("verify", "--store", store)
    assert code == 1 and doc["ok"] is False


def test_store_from_environment(tmp_path: Path) -> None:
    import os

    env = {**os.environ, "BLUX_SYSTEM_STORE": str(tmp_path / "env-store")}
    assert run("init", env=env)[0] == 0
    assert run("list", env=env)[1]["count"] == 0


def test_legacy_commands_still_write_files(tmp_path: Path) -> None:
    (tmp_path / "in").mkdir()
    (tmp_path / "in" / "a.txt").write_text("a")
    out = tmp_path / "out"
    assert (
        subprocess.run([sys.executable, "-m", "blux_system.cli", "snapshot", "--in", str(tmp_path / "in"), "--out", str(out)]).returncode
        == 0
    )
    assert (
        subprocess.run(
            [sys.executable, "-m", "blux_system.cli", "receipt", "--snapshot", str(out / "snapshot.json"), "--out", str(out)]
        ).returncode
        == 0
    )
    assert json.loads((out / "receipt.json").read_text())["receipt_hash"].startswith("sha256:")


# ---------------------------------------------------------------- contract dependency rules
def test_contract_v1_comes_from_blux_sdk_not_a_copy() -> None:
    """Stage 02 rule: use Contract v1 through a stable dependency; never fork the schemas."""
    copied = [
        p
        for p in ROOT.rglob("*.schema.json")
        if p.name in {"receipt.schema.json", "common.schema.json", "artifact.schema.json", "execution.schema.json"}
    ]
    assert copied == []
    deps = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"]
    sdk = [d for d in deps if d.startswith("blux-sdk")]
    assert len(sdk) == 1
    assert re.search(r"git\+https://github\.com/Outer-Void/blux\.git@[0-9a-f]{40}#subdirectory=sdk/python$", sdk[0]), sdk[0]


def test_sdk_speaks_contract_1_0() -> None:
    import blux_sdk

    assert "1.0" in blux_sdk.SUPPORTED_CONTRACT_VERSIONS


def test_legacy_schemas_are_packaged() -> None:
    from blux_system.legacy.core import _load_schema

    assert _load_schema("execution_receipt.schema.json")["type"] == "object"
