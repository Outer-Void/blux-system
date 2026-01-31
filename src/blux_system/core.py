from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

CONTRACT_VERSION = "1.0"
DEFAULT_ORDERING = {
    "outputs": "path",
    "output_bundles": "bundle_id",
    "patch_bundles": "bundle_id",
    "output_hashes": "path",
    "run_graph_steps": "id",
}


@dataclass(frozen=True)
class FileRecord:
    path: str
    hash: str
    size: int

    def as_dict(self) -> dict[str, object]:
        return {"path": self.path, "hash": self.hash, "size": self.size}


def canonical_json_bytes(data: object) -> bytes:
    return json.dumps(
        data,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _hash_bytes(data: bytes) -> str:
    digest = hashlib.sha256(data).hexdigest()
    return f"sha256:{digest}"


def _hash_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8192), b""):
            hasher.update(chunk)
    return f"sha256:{hasher.hexdigest()}"


def _deterministic_timestamp() -> str:
    override = os.getenv("BLUX_DETERMINISTIC_TIMESTAMP")
    if override:
        return override
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _sorted_file_records(paths: Iterable[Path], base: Path) -> list[FileRecord]:
    records = []
    for path in paths:
        relative = path.relative_to(base).as_posix()
        records.append(
            FileRecord(
                path=relative,
                hash=_hash_file(path),
                size=path.stat().st_size,
            )
        )
    return sorted(records, key=lambda record: record.path)


def _collect_files(root: Path) -> list[FileRecord]:
    root = root.resolve()
    if root.is_file():
        return _sorted_file_records([root], root.parent)
    paths = sorted(p for p in root.rglob("*") if p.is_file())
    return _sorted_file_records(paths, root)


def _normalize_file_records(records: Sequence[FileRecord] | Sequence[dict[str, object]]) -> list[dict[str, object]]:
    normalized = []
    for record in records:
        if isinstance(record, FileRecord):
            normalized.append(record.as_dict())
        else:
            normalized.append(
                {
                    "path": record["path"],
                    "hash": record["hash"],
                    "size": record.get("size", 0),
                }
            )
    return sorted(normalized, key=lambda item: item["path"])


def _normalize_output_bundles(bundles: Sequence[dict[str, object]]) -> list[dict[str, object]]:
    normalized = []
    for bundle in bundles:
        normalized.append(
            {
                "bundle_id": bundle["bundle_id"],
                "files": _normalize_file_records(bundle.get("files", [])),
            }
        )
    return sorted(normalized, key=lambda item: item["bundle_id"])


def _normalize_patch_bundles(bundles: Sequence[dict[str, object]]) -> list[dict[str, object]]:
    normalized = []
    for bundle in bundles:
        normalized.append(
            {
                "bundle_id": bundle["bundle_id"],
                "base_path": bundle["base_path"],
                "patches": _normalize_file_records(bundle.get("patches", [])),
                "outputs": _normalize_file_records(bundle.get("outputs", [])),
            }
        )
    return sorted(normalized, key=lambda item: item["bundle_id"])


def make_snapshot(
    inputs: Sequence[FileRecord] | Sequence[dict[str, object]],
    outputs: Sequence[FileRecord] | Sequence[dict[str, object]],
    *,
    output_bundles: Sequence[dict[str, object]] | None = None,
    patch_bundles: Sequence[dict[str, object]] | None = None,
    created_at: str | None = None,
    contract_version: str = CONTRACT_VERSION,
) -> dict[str, object]:
    created = created_at or _deterministic_timestamp()
    normalized_output_bundles = _normalize_output_bundles(output_bundles or [])
    normalized_patch_bundles = _normalize_patch_bundles(patch_bundles or [])

    payload = {
        "contract_version": contract_version,
        "created_at": created,
        "inputs": _normalize_file_records(inputs),
        "outputs": _normalize_file_records(outputs),
        "output_bundles": normalized_output_bundles,
        "patch_bundles": normalized_patch_bundles,
    }
    snapshot_hash = _hash_bytes(canonical_json_bytes(payload))
    payload["snapshot_hash"] = snapshot_hash
    return payload


def _default_agent_headers() -> dict[str, str]:
    return {
        "input_hash": os.getenv("BLUX_AGENT_INPUT_HASH", "unknown"),
        "model_version": os.getenv("BLUX_AGENT_MODEL_VERSION", "unknown"),
        "contract_version": os.getenv("BLUX_AGENT_CONTRACT_VERSION", "unknown"),
        "requested_model_version": os.getenv("BLUX_AGENT_REQUESTED_MODEL_VERSION", "unknown"),
        "resolved_model_version": os.getenv("BLUX_AGENT_RESOLVED_MODEL_VERSION", "unknown"),
        "schema_version": os.getenv("BLUX_AGENT_SCHEMA_VERSION", "unknown"),
    }


def _collect_output_hashes(snapshot: dict[str, object]) -> list[dict[str, object]]:
    output_records: list[dict[str, object]] = []
    output_records.extend(_normalize_file_records(snapshot.get("outputs", [])))
    for bundle in snapshot.get("output_bundles", []) or []:
        output_records.extend(_normalize_file_records(bundle.get("files", [])))
    for bundle in snapshot.get("patch_bundles", []) or []:
        output_records.extend(_normalize_file_records(bundle.get("patches", [])))
        output_records.extend(_normalize_file_records(bundle.get("outputs", [])))
    return sorted(output_records, key=lambda item: (item["path"], item["hash"]))


def _normalize_run_steps(steps: Sequence[dict[str, object]]) -> list[dict[str, object]]:
    normalized = []
    for step in steps:
        normalized.append(
            {
                "id": step["id"],
                "agent": step["agent"],
                "input_ref": step["input_ref"],
                "output_ref": step["output_ref"],
                "status": step["status"],
            }
        )
    return sorted(normalized, key=lambda item: item["id"])


def _normalize_pack(pack: dict[str, str] | None) -> dict[str, str] | None:
    if not pack:
        return None
    return {"id": pack["id"], "version": pack["version"]}


def _normalize_dataset_fixture(fixture: dict[str, object] | None) -> dict[str, object] | None:
    if not fixture:
        return None
    normalized = {"id": fixture["id"]}
    if "hash" in fixture:
        normalized["hash"] = fixture["hash"]
    if "path" in fixture:
        normalized["path"] = fixture["path"]
    return normalized


def make_receipt(
    snapshot: dict[str, object],
    *,
    agent_headers: dict[str, str] | None = None,
    created_at: str | None = None,
    contract_version: str = CONTRACT_VERSION,
    policy_pack: dict[str, str] | None = None,
    reasoning_pack: dict[str, str] | None = None,
    run_steps: Sequence[dict[str, object]] | None = None,
    dataset_fixture: dict[str, object] | None = None,
) -> dict[str, object]:
    created = created_at or _deterministic_timestamp()
    snapshot_hash = snapshot.get("snapshot_hash")
    if not snapshot_hash:
        snapshot_hash = _hash_bytes(
            canonical_json_bytes({k: v for k, v in snapshot.items() if k != "snapshot_hash"})
        )
    agent = agent_headers or _default_agent_headers()
    output_hashes = _collect_output_hashes(snapshot)

    payload = {
        "contract_version": contract_version,
        "created_at": created,
        "agent": {
            "input_hash": agent["input_hash"],
            "model_version": agent["model_version"],
            "contract_version": agent["contract_version"],
            "requested_model_version": agent.get("requested_model_version", "unknown"),
            "resolved_model_version": agent.get("resolved_model_version", "unknown"),
            "schema_version": agent.get("schema_version", "unknown"),
        },
        "snapshot": {
            "hash": snapshot_hash,
            "contract_version": snapshot.get("contract_version"),
        },
        "snapshot_hash": snapshot_hash,
        "output_hashes": output_hashes,
        "ordering": DEFAULT_ORDERING,
    }
    normalized_policy_pack = _normalize_pack(policy_pack)
    if normalized_policy_pack:
        payload["policy_pack"] = normalized_policy_pack
    normalized_reasoning_pack = _normalize_pack(reasoning_pack)
    if normalized_reasoning_pack:
        payload["reasoning_pack"] = normalized_reasoning_pack
    if run_steps is not None:
        payload["run_graph"] = {"steps": _normalize_run_steps(run_steps)}
    normalized_fixture = _normalize_dataset_fixture(dataset_fixture)
    if normalized_fixture:
        payload["dataset_fixture"] = normalized_fixture
    receipt_hash = _hash_bytes(canonical_json_bytes(payload))
    payload["receipt_hash"] = receipt_hash
    return payload


def load_state(path: str | Path) -> dict[str, object]:
    data = Path(path).read_text(encoding="utf-8")
    return json.loads(data)


def save_state(path: str | Path, state: dict[str, object]) -> None:
    content = canonical_json_bytes(state)
    Path(path).write_bytes(content)


def build_snapshot_from_dirs(input_dir: Path, output_dir: Path) -> dict[str, object]:
    inputs = _collect_files(input_dir)
    outputs = _collect_files(output_dir)
    return make_snapshot(inputs, outputs)


def build_receipt_from_snapshot(snapshot_path: Path) -> dict[str, object]:
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    return make_receipt(snapshot)


def _load_schema(name: str) -> dict[str, object]:
    root = Path(__file__).resolve().parents[2]
    schema_path = root / "schemas" / name
    return json.loads(schema_path.read_text(encoding="utf-8"))


def _validate_schema(payload: dict[str, object], schema_name: str) -> tuple[bool, str | None]:
    import jsonschema

    schema = _load_schema(schema_name)
    try:
        jsonschema.validate(payload, schema)
        return True, None
    except jsonschema.ValidationError as exc:
        return False, exc.message


def _validate_receipt_hash(receipt: dict[str, object]) -> bool:
    expected = receipt.get("receipt_hash")
    payload = {k: v for k, v in receipt.items() if k != "receipt_hash"}
    calculated = _hash_bytes(canonical_json_bytes(payload))
    return expected == calculated


def build_replay_report(receipt_path: Path, root_dir: Path) -> dict[str, object]:
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    schema_valid, schema_error = _validate_schema(receipt, "execution_receipt.schema.json")
    receipt_hash_match = _validate_receipt_hash(receipt) if schema_valid else False

    output_entries = receipt.get("output_hashes", []) if isinstance(receipt, dict) else []
    output_entries = _normalize_file_records(output_entries)
    output_results = []
    missing_count = 0
    mismatch_count = 0
    for entry in output_entries:
        path = entry["path"]
        expected_hash = entry["hash"]
        file_path = root_dir / path
        exists = file_path.exists()
        actual_hash = _hash_file(file_path) if exists else None
        hash_match = exists and actual_hash == expected_hash
        if not exists:
            missing_count += 1
        if exists and not hash_match:
            mismatch_count += 1
        output_results.append(
            {
                "path": path,
                "expected_hash": expected_hash,
                "actual_hash": actual_hash,
                "exists": exists,
                "hash_match": hash_match,
            }
        )

    dataset_fixture = receipt.get("dataset_fixture") if isinstance(receipt, dict) else None
    fixture_result = None
    fixture_missing = 0
    fixture_mismatch = 0
    if isinstance(dataset_fixture, dict):
        fixture_path_value = dataset_fixture.get("path")
        fixture_path = root_dir / fixture_path_value if fixture_path_value else None
        exists = fixture_path.exists() if fixture_path else None
        actual_hash = _hash_file(fixture_path) if fixture_path and exists else None
        expected_hash = dataset_fixture.get("hash")
        hash_match = None
        if expected_hash is not None:
            hash_match = exists and actual_hash == expected_hash if exists is not None else None
            if exists is False:
                fixture_missing = 1
            if exists and hash_match is False:
                fixture_mismatch = 1
        fixture_result = {
            "id": dataset_fixture.get("id"),
            "path": fixture_path_value,
            "expected_hash": expected_hash,
            "exists": exists,
            "actual_hash": actual_hash,
            "hash_match": hash_match,
        }

    summary = {
        "ok": (
            schema_valid
            and receipt_hash_match
            and missing_count == 0
            and mismatch_count == 0
            and fixture_missing == 0
            and fixture_mismatch == 0
        ),
        "total_outputs": len(output_results),
        "missing_outputs": missing_count,
        "hash_mismatches": mismatch_count,
        "fixture_missing": fixture_missing,
        "fixture_hash_mismatches": fixture_mismatch,
    }

    payload = {
        "contract_version": CONTRACT_VERSION,
        "created_at": _deterministic_timestamp(),
        "receipt_path": receipt_path.as_posix(),
        "root": root_dir.as_posix(),
        "schema_valid": schema_valid,
        "schema_error": schema_error,
        "receipt_hash_match": receipt_hash_match,
        "output_results": output_results,
        "dataset_fixture_result": fixture_result,
        "summary": summary,
    }
    report_hash = _hash_bytes(canonical_json_bytes(payload))
    payload["report_hash"] = report_hash
    return payload
