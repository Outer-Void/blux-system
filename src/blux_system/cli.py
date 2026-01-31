from __future__ import annotations

import argparse
from pathlib import Path

from blux_system.core import build_receipt_from_snapshot, build_snapshot_from_dirs, canonical_json_bytes


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_bytes(canonical_json_bytes(payload))


def snapshot_command(args: argparse.Namespace) -> int:
    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    snapshot = build_snapshot_from_dirs(input_dir, output_dir)
    _write_json(output_dir / "snapshot.json", snapshot)
    return 0


def receipt_command(args: argparse.Namespace) -> int:
    snapshot_path = Path(args.snapshot)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    receipt = build_receipt_from_snapshot(snapshot_path)
    _write_json(output_dir / "receipt.json", receipt)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="blux-system", description="BLUX deterministic snapshots and receipts")
    subparsers = parser.add_subparsers(dest="command", required=True)

    snapshot_parser = subparsers.add_parser("snapshot", help="Record deterministic snapshot data")
    snapshot_parser.add_argument("--in", dest="input_dir", required=True, help="Input directory")
    snapshot_parser.add_argument("--out", dest="output_dir", required=True, help="Output directory")
    snapshot_parser.set_defaults(func=snapshot_command)

    receipt_parser = subparsers.add_parser("receipt", help="Record deterministic receipt data")
    receipt_parser.add_argument("--snapshot", required=True, help="Snapshot file")
    receipt_parser.add_argument("--out", dest="output_dir", required=True, help="Output directory")
    receipt_parser.set_defaults(func=receipt_command)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
