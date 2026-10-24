"""Operation graph reconstruction from receipts (parent_operation_id) and cancellation events.

Nodes are operations. Edges are parent -> child. A parent that is referenced but has no record of its own
becomes a placeholder node (`missing: true`, no status). Cycles, which valid producers never create, are
reported and their members are treated as roots so nothing is silently dropped.
"""

from __future__ import annotations

from typing import Any

from blux_sdk.timeutil import parse_timestamp, utc_now

from blux_system.statuses import Status, aggregate, status_of_receipt
from blux_system.store import ReceiptStore


def _node(operation_id: str) -> dict[str, Any]:
    return {
        "operation_id": operation_id,
        "parent_operation_id": None,
        "receipt_id": None,
        "capability_id": None,
        "capability_version": None,
        "session_id": None,
        "project_id": None,
        "own_status": None,
        "aggregate_status": None,
        "error_code": None,
        "started_at": None,
        "finished_at": None,
        "artifact_ids": [],
        "cancelled": None,
        "missing": True,
        "children": [],
    }


def build_graph(store: ReceiptStore, *, root_operation_id: str | None = None, session_id: str | None = None) -> dict[str, Any]:
    nodes: dict[str, dict[str, Any]] = {}
    for r in store.receipts(session_id=session_id):
        n = nodes.setdefault(r["operation_id"], _node(r["operation_id"]))
        n.update(
            parent_operation_id=r["parent_operation_id"],
            receipt_id=r["receipt_id"],
            capability_id=r["capability_id"],
            capability_version=r["capability_version"],
            session_id=r["session_id"],
            project_id=r["project_id"],
            own_status=status_of_receipt(r).value,
            error_code=r["error_code"],
            started_at=r["started_at"],
            finished_at=r["finished_at"],
            artifact_ids=list(r["artifact_ids"]),
            missing=False,
        )
    for e in store.events():
        if e["type"] != "cancelled":
            continue
        data = e.get("data", {})
        if session_id is not None and data.get("session_id") != session_id:
            continue
        op = e["operation_id"]
        n = nodes.setdefault(op, _node(op))
        if n["receipt_id"] is None:
            n.update(
                parent_operation_id=data.get("parent_operation_id"),
                session_id=data.get("session_id"),
                own_status=Status.CANCELLED.value,
                cancelled={"reason": data.get("reason"), "at": e["at"]},
                missing=False,
            )
    for a in store.artifacts():
        holder = nodes.get(a["operation_id"])
        if holder is not None and a["artifact_id"] not in holder["artifact_ids"]:
            holder["artifact_ids"].append(a["artifact_id"])

    # placeholders for referenced-but-unknown parents
    for op in list(nodes):
        parent = nodes[op]["parent_operation_id"]
        if parent is not None and parent not in nodes:
            nodes[parent] = _node(parent)
    edges = []
    for op, n in nodes.items():
        parent = n["parent_operation_id"]
        if parent is not None:
            nodes[parent]["children"].append(op)
            edges.append([parent, op])
    for n in nodes.values():
        n["children"].sort(key=lambda c: (nodes[c]["started_at"] or "", c))

    cycles = _find_cycles(nodes)
    in_cycle = {op for cyc in cycles for op in cyc}
    roots = sorted(
        (op for op, n in nodes.items() if n["parent_operation_id"] is None or op in in_cycle),
        key=lambda op: (nodes[op]["started_at"] or "", op),
    )

    def agg(op: str, seen: frozenset[str]) -> Status | None:
        """Aggregate of op's subtree. A child's aggregate is non-SUCCESS iff something below it is."""
        n = nodes[op]
        own = Status(n["own_status"]) if n["own_status"] else None
        child_aggs = [agg(c, seen | {c}) for c in n["children"] if c not in seen]
        result = None if n["missing"] else aggregate(own, child_aggs)
        n["aggregate_status"] = result.value if result else None
        return result

    for root in roots:
        agg(root, frozenset({root}))

    if root_operation_id is not None:
        if root_operation_id not in nodes:
            return {
                "format": "blux-system.graph",
                "version": 1,
                "generated_at": utc_now(),
                "roots": [],
                "nodes": {},
                "edges": [],
                "cycles": [],
            }
        keep = _subtree(nodes, root_operation_id)
        nodes = {k: v for k, v in nodes.items() if k in keep}
        edges = [e for e in edges if e[0] in keep and e[1] in keep]
        roots = [root_operation_id]
        cycles = [c for c in cycles if set(c) & keep]

    return {
        "format": "blux-system.graph",
        "version": 1,
        "generated_at": utc_now(),
        "roots": roots,
        "nodes": nodes,
        "edges": sorted(edges),
        "cycles": cycles,
    }


def _subtree(nodes: dict[str, dict[str, Any]], root: str) -> set[str]:
    out, stack = set(), [root]
    while stack:
        op = stack.pop()
        if op in out:
            continue
        out.add(op)
        stack.extend(nodes[op]["children"])
    return out


def _find_cycles(nodes: dict[str, dict[str, Any]]) -> list[list[str]]:
    cycles: list[list[str]] = []
    state: dict[str, int] = {}
    for start in sorted(nodes):
        path: list[str] = []
        op: str | None = start
        while op is not None and state.get(op, 0) == 0:
            state[op] = 1
            path.append(op)
            op = nodes[op]["parent_operation_id"]
        if op is not None and state.get(op) == 1 and op in path:
            cycles.append(sorted(path[path.index(op) :]))
        for p in path:
            state[p] = 2
    return cycles


def timeline(store: ReceiptStore, *, operation_id: str | None = None, session_id: str | None = None) -> list[dict[str, Any]]:
    """Chronological events. With operation_id, includes the whole subtree under it."""
    scope: set[str] | None = None
    if operation_id is not None or session_id is not None:
        g = build_graph(store, root_operation_id=operation_id, session_id=session_id)
        scope = set(g["nodes"])
    items: list[dict[str, Any]] = []
    for r in store.receipts():
        if scope is not None and r["operation_id"] not in scope:
            continue
        base = {"operation_id": r["operation_id"], "receipt_id": r["receipt_id"], "capability_id": r["capability_id"]}
        items.append({**base, "at": r["started_at"], "type": "started"})
        items.append(
            {**base, "at": r["finished_at"], "type": "finished", "status": status_of_receipt(r).value, "error_code": r["error_code"]}
        )
    for e in store.events():
        if scope is not None and e["operation_id"] not in scope:
            continue
        items.append(
            {"operation_id": e["operation_id"], "event_id": e["event_id"], "at": e["at"], "type": e["type"], "data": e.get("data", {})}
        )
    for a in store.artifacts():
        if scope is not None and a["operation_id"] not in scope:
            continue
        items.append(
            {
                "operation_id": a["operation_id"],
                "artifact_id": a["artifact_id"],
                "at": a["created_at"],
                "type": "artifact",
                "sha256": a["sha256"],
            }
        )
    order = {"started": 0, "artifact": 1, "note": 1, "finished": 2, "cancelled": 2}
    return sorted(items, key=lambda i: (parse_timestamp(i["at"]), order.get(i["type"], 1), i["operation_id"]))
