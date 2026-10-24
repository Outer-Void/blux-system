"""`blux-system` command line.

Store commands follow the BLUX machine protocol (blux ADR-0008): exactly one JSON document on stdout,
diagnostics on stderr, exit 0 ok / 1 failure (an error document is still printed) / 2 usage.
The legacy `snapshot`, `receipt` and `replay` commands keep their original file-writing behaviour.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, NoReturn, TextIO

from blux_sdk.errors import BluxError
from blux_sdk.serialization import dumps_json, loads_json

from blux_system import __version__
from blux_system.errors import StoreError, StoreErrorCode
from blux_system.graph import build_graph, timeline
from blux_system.legacy.core import (
    build_receipt_from_snapshot,
    build_replay_report,
    build_snapshot_from_dirs,
    canonical_json_bytes,
)
from blux_system.statuses import Status
from blux_system.store import ReceiptStore

EXIT_OK, EXIT_FAILED, EXIT_USAGE = 0, 1, 2


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        print(f"blux-system: error: {message}", file=sys.stderr)
        raise SystemExit(EXIT_USAGE)


# ---------------------------------------------------------------- legacy commands (unchanged behaviour)
def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_bytes(canonical_json_bytes(payload))


def snapshot_command(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "snapshot.json", build_snapshot_from_dirs(Path(args.input_dir), output_dir))
    return 0


def receipt_command(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "receipt.json", build_receipt_from_snapshot(Path(args.snapshot)))
    return 0


def replay_command(args: argparse.Namespace) -> int:
    root_dir = Path(args.root)
    root_dir.mkdir(parents=True, exist_ok=True)
    _write_json(root_dir / "replay_report.json", build_replay_report(Path(args.receipt), root_dir))
    return 0


# ---------------------------------------------------------------- store commands
def _read_input(source: str, stdin: TextIO) -> bytes:
    if source == "-":
        data = stdin.buffer.read() if hasattr(stdin, "buffer") else stdin.read().encode("utf-8")
        return data
    try:
        return Path(source).read_bytes()
    except OSError as exc:
        raise StoreError(StoreErrorCode.NOT_FOUND, "Cannot read input file.", details={"errno": exc.errno}) from None


def _store_path(args: argparse.Namespace) -> str:
    path = args.store or os.environ.get("BLUX_SYSTEM_STORE")
    if not path:
        raise StoreError(StoreErrorCode.STORE_NOT_FOUND, "No store given (--store or BLUX_SYSTEM_STORE).")
    return str(path)


def _open(args: argparse.Namespace, *, read_only: bool = False) -> ReceiptStore:
    return ReceiptStore.open(_store_path(args), read_only=read_only)


def _dispatch_store(args: argparse.Namespace, stdin: TextIO) -> tuple[int, Any]:
    cmd = args.command
    if cmd == "init":
        policy = loads_json(_read_input(args.policy, stdin)) if args.policy else None
        store = ReceiptStore.init(_store_path(args), policy=policy)
        return EXIT_OK, {"initialized": str(store.root), "retention": store.policy.to_document()}
    if cmd == "ingest":
        store = _open(args)
        doc = loads_json(_read_input(args.file, stdin))
        if args.kind == "artifact":
            data = Path(args.data).read_bytes() if args.data else None
            return EXIT_OK, store.ingest_artifact(doc, data)
        return EXIT_OK, store.ingest_receipt(doc)
    if cmd == "cancel":
        return EXIT_OK, _open(args).cancel(args.operation_id, reason=args.reason, parent_operation_id=args.parent, session_id=args.session)
    if cmd == "note":
        return EXIT_OK, _open(args).record_event(args.operation_id, "note", loads_json(args.data))
    if cmd == "show":
        store = _open(args, read_only=True)
        if args.receipt:
            return EXIT_OK, store.get_receipt(args.receipt)
        r = store.receipt_for_operation(args.operation)
        if r is None:
            raise StoreError(StoreErrorCode.NOT_FOUND, "No receipt for operation.", details={"operation_id": args.operation})
        return EXIT_OK, r
    if cmd == "list":
        store = _open(args, read_only=True)
        rs = store.receipts(
            session_id=args.session,
            project_id=args.project,
            capability_id=args.capability,
            status=args.status,
            since=args.since,
            until=args.until,
        )
        return EXIT_OK, {"count": len(rs), "receipts": rs}
    if cmd == "graph":
        return EXIT_OK, build_graph(_open(args, read_only=True), root_operation_id=args.root, session_id=args.session)
    if cmd == "timeline":
        items = timeline(_open(args, read_only=True), operation_id=args.operation, session_id=args.session)
        return EXIT_OK, {"count": len(items), "timeline": items}
    if cmd == "verify":
        report = ReceiptStore(Path(_store_path(args)), read_only=True).verify() if Path(_store_path(args), "store.json").is_file() else None
        if report is None:
            raise StoreError(StoreErrorCode.STORE_NOT_FOUND, "No BLUX System store here.")
        return (EXIT_OK if report.ok else EXIT_FAILED), report.to_document()
    if cmd == "export":
        store = _open(args, read_only=True)
        with Path(args.out).open("wb") as fh:
            header = store.export(fh)
        return EXIT_OK, {"exported": args.out, **header}
    if cmd == "import":
        return EXIT_OK, _open(args).import_bundle(_read_input(args.file, stdin))
    if cmd == "retention":
        store = _open(args)
        return EXIT_OK, store.apply_retention() if args.apply else {"policy": store.policy.to_document()}
    if cmd == "artifact-verify":
        res = _open(args, read_only=True).verify_artifact(args.artifact_id, args.file)
        return (EXIT_OK if res["ok"] else EXIT_FAILED), res
    if cmd == "replay-check":
        store = _open(args, read_only=True)
        output_doc = loads_json(_read_input(args.output, stdin)) if args.output else None
        input_doc = loads_json(_read_input(args.input, stdin)) if args.input else None
        if output_doc is None and input_doc is None:
            return EXIT_OK, store.replay_metadata(args.operation_id)
        res = store.verify_replay(args.operation_id, input_doc=input_doc, output_doc=output_doc)
        return (EXIT_OK if res["ok"] else EXIT_FAILED), res
    if cmd == "version":
        from blux_sdk import CONTRACT_VERSION, SUPPORTED_CONTRACT_VERSIONS
        from blux_sdk import __version__ as sdk_version

        return EXIT_OK, {
            "blux_system": __version__,
            "blux_sdk": sdk_version,
            "contract_version": CONTRACT_VERSION,
            "supported_contract_versions": list(SUPPORTED_CONTRACT_VERSIONS),
            "statuses": [s.value for s in Status],
        }
    raise AssertionError(cmd)  # pragma: no cover


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="blux-system", description="BLUX System: execution truth store (receipts, graphs, timelines).")
    parser.add_argument("--pretty", action="store_true", help="indent JSON output")
    sub = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)

    def store_cmd(name: str, help_: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_)
        p.add_argument("--store", help="store directory (default: $BLUX_SYSTEM_STORE)")
        return p

    p = store_cmd("init", "create a store")
    p.add_argument("--policy", help="retention policy JSON")
    p = store_cmd("ingest", "store a blux.receipt (or blux.artifact) document")
    p.add_argument("file", help="path or - for stdin")
    p.add_argument("--kind", choices=["receipt", "artifact"], default="receipt")
    p.add_argument("--data", help="artifact bytes to verify and keep as a blob")
    p = store_cmd("cancel", "record that an operation was cancelled before it produced a receipt")
    p.add_argument("operation_id")
    p.add_argument("--reason", required=True)
    p.add_argument("--parent")
    p.add_argument("--session")
    p = store_cmd("note", "attach a note event to an operation")
    p.add_argument("operation_id")
    p.add_argument("--data", default="{}", help="JSON object")
    p = store_cmd("show", "print one receipt")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--receipt")
    g.add_argument("--operation")
    p = store_cmd("list", "list receipts")
    for opt in ("--session", "--project", "--capability", "--since", "--until"):
        p.add_argument(opt)
    p.add_argument("--status", choices=[s.value for s in Status if s is not Status.PARTIAL])
    p = store_cmd("graph", "reconstruct the operation graph")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--root")
    g.add_argument("--session")
    p = store_cmd("timeline", "chronological event timeline")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--operation")
    g.add_argument("--session")
    store_cmd("verify", "verify the log hash chain (read-only)")
    p = store_cmd("export", "write a verifiable JSONL bundle")
    p.add_argument("out")
    p = store_cmd("import", "import a bundle (idempotent)")
    p.add_argument("file")
    p = store_cmd("retention", "show or apply the retention policy")
    p.add_argument("--apply", action="store_true")
    p = store_cmd("artifact-verify", "verify artifact bytes against the stored hash")
    p.add_argument("artifact_id")
    p.add_argument("--file")
    p = store_cmd("replay-check", "replay metadata, or compare a re-run's input/output hashes")
    p.add_argument("operation_id")
    p.add_argument("--output", help="JSON output document of a re-run")
    p.add_argument("--input", help="JSON input document")
    sub.add_parser("version", help="versions and status vocabulary")

    lp = sub.add_parser("snapshot", help="[legacy] record deterministic snapshot data")
    lp.add_argument("--in", dest="input_dir", required=True)
    lp.add_argument("--out", dest="output_dir", required=True)
    lp.set_defaults(legacy=snapshot_command)
    lp = sub.add_parser("receipt", help="[legacy] record a pre-Contract-v1 receipt")
    lp.add_argument("--snapshot", required=True)
    lp.add_argument("--out", dest="output_dir", required=True)
    lp.set_defaults(legacy=receipt_command)
    lp = sub.add_parser("replay", help="[legacy] replay-verify a legacy receipt")
    lp.add_argument("--receipt", required=True)
    lp.add_argument("--root", required=True)
    lp.set_defaults(legacy=replay_command)
    return parser


def main(argv: list[str] | None = None, *, stdin: TextIO | None = None, stdout: TextIO | None = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "legacy", None):
        return int(args.legacy(args))
    out = stdout or sys.stdout
    try:
        code, doc = _dispatch_store(args, stdin or sys.stdin)
    except StoreError as exc:
        code, doc = EXIT_FAILED, exc.to_document()
    except BluxError as exc:
        code, doc = (
            EXIT_FAILED,
            StoreError(StoreErrorCode.INVALID_RECORD, exc.message, details={"contract_error": exc.to_document()}).to_document(),
        )
    out.write(dumps_json(doc, pretty=args.pretty) + "\n")
    out.flush()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
