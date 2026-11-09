"""OV-62 fault drills (F135): an I/O failure is a STORE_IO_ERROR document, never a silent death or a false
`stored`, and the store stays recoverable.

On 26f2240 the ingest under a file-size limit printed a traceback and nothing on stdout (the one-document
protocol was broken), and with the default SIGXFSZ action the process was killed outright.
"""

from __future__ import annotations

import json
import resource
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from blux_system.store import ReceiptStore
from tests.conftest import receipt

CLI = [sys.executable, "-m", "blux_system.cli"]


def _run(args: list[str], data: bytes | None = None, limit: int | None = None) -> tuple[int, dict]:  # type: ignore[type-arg]
    def pre() -> None:
        signal.signal(signal.SIGXFSZ, signal.SIG_DFL)  # the default action: the limit kills the process
        if limit is not None:
            resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))

    proc = subprocess.run([*CLI, *args], input=data, capture_output=True, preexec_fn=pre, check=False)
    assert proc.stdout, f"no JSON document (exit {proc.returncode}): {proc.stderr[-300:]!r}"
    return proc.returncode, json.loads(proc.stdout)


@pytest.mark.skipif(not hasattr(signal, "SIGXFSZ"), reason="POSIX only")
def test_a_size_limit_during_ingest_is_a_store_io_error_and_the_store_recovers(tmp_path: Path) -> None:
    root = tmp_path / "s"
    assert _run(["init", "--store", str(root)])[0] == 0
    first = receipt()
    assert _run(["ingest", "--store", str(root), "-"], json.dumps(first).encode())[1]["status"] == "stored"
    log_size = max(p.stat().st_size for p in root.rglob("*") if p.is_file())
    big = receipt()
    code, doc = _run(["ingest", "--store", str(root), "-"], json.dumps(big).encode(), limit=log_size + 64)
    assert code == 1
    assert doc["code"] == "STORE_IO_ERROR" and doc["details"]["error"] == "EFBIG"
    # Nothing claimed, nothing lost: the first receipt is intact and the next write works.
    code, doc = _run(["ingest", "--store", str(root), "-"], json.dumps(big).encode())
    assert code == 0 and doc["status"] == "stored"
    store = ReceiptStore.open(root, read_only=True)
    assert store.verify().ok
    assert {r["receipt_id"] for r in store.receipts()} == {first["receipt_id"], big["receipt_id"]}
