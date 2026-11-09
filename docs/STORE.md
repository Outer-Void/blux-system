# Receipt store (format 1)

## Layout
```
<store>/
  store.json        {"format": "blux-system-store", "version": 1, "created_at": ...}
  retention.json    blux-system.retention v1 policy
  log.jsonl         append-only, hash-chained records
  head.json         {"seq", "hash"} of the last record: detects a shortened or swapped log (OV-52)
  blobs/sha256/xx/  optional artifact bytes (content-addressed)
  .pseudonym.key    HMAC key (0600), only when pseudonymize_actor_ids is on; never exported
  .lock             writer lock (POSIX flock)
```

## Records
Each line of `log.jsonl` is:
```
{"seq": n, "kind": receipt|artifact|event|retention|recovery, "at": <utc>, "payload": {...},
 "meta": {...}, "prev": <hash of record n-1 | 64 zeros>, "hash": sha256(canonical(body without hash))}
```

Payloads by kind:
- `receipt`: a redacted Contract v1 `blux.receipt`. It is still schema-valid.
- `artifact`: a `blux.artifact`.
- `event`: `{event_id, operation_id, type: cancelled|note, data}`.
- `retention` / `recovery`: maintenance records.

`meta` holds `redacted`, `redaction_rules` and `source`. It also holds `content_sha256`, but only when nothing
was redacted, so a fingerprint can never reveal redacted content.

## Immutability
- Writes only append. Each append runs under an exclusive lock and first reloads the log, so two writers
  cannot fork the chain or race a conflict check. A writer waits up to 10 s for the lock (appends take
  milliseconds), so simultaneous writers queue instead of being refused.
- A receipt is immutable:
  - an identical re-ingest → `duplicate` (no-op);
  - different content under the same `receipt_id`, or a second receipt for the same `operation_id` → `RECEIPT_CONFLICT`.
- Cancellation is allowed only before a receipt exists. A receipt for a cancelled operation → `ALREADY_FINISHED`.
- **Retention compaction is the only rewrite.** It is explicit. The new chain ends with its own `retention`
  record (what was removed, the cutoffs, and the `previous_head` and `previous_records` of the chain it
  replaces) and is installed with one atomic rename, so a crash leaves the old log or the new one, never an
  unrecorded removal.

## Corruption and failure handling
| Condition | Behaviour |
|---|---|
| Unterminated last line (crash mid-append) | A writable open, or the next append, truncates exactly that line and appends a `recovery` record (`torn_tail`). A read-only open serves the intact prefix |
| Complete last record whose newline was not written | The newline is added and a `recovery` record (`unterminated_record`) appended. Before OV-52 the next append made the whole store `STORE_CORRUPT` |
| Edited record, deleted/reordered line, garbage | `STORE_CORRUPT` with `{line, seq, reason}`: `hash_mismatch`, `sequence_gap`, `broken_chain` or `unparseable`. Nothing is repaired automatically |
| Whole records cut from the end, or an older copy of the log | `truncated`: the log is shorter than `head.json` says |
| A different log of the same or greater length | `anchor_mismatch`: the anchored record is not in the log |
| A record whose hashes were recomputed but whose payload is not what the index needs | `invalid_payload` with a `detail`, on every open. `verify` also checks receipts and artifacts against Contract v1 |
| Log changed by another program between writes | Detected before the next append (`STORE_CORRUPT`) |
| Bad `store.json`, unreadable `retention.json` | `STORE_CORRUPT` (`details.reason`). A store written by a newer format is `unsupported_version` and is neither read nor written |
| Artifact blob changed or deleted | `verify` reports `blob_mismatch` / `blob_missing`; re-ingesting the artifact with its bytes repairs the blob |
| Writer lock held for more than 10 s | `STORE_LOCKED` |
| Disk full, a file-size limit or a permission error while reading or writing | `STORE_IO_ERROR` with `details.errno` and `details.error` (for example `EFBIG`, `ENOSPC`). Nothing is reported as stored; a half-written last record is a torn tail, repaired by the next write. Before OV-62 the process died with a traceback and no JSON document |

`blux-system verify` runs read-only and checks the chain, the anchor, every payload and every blob.
`verify --expect-head HASH` also fails unless the chain contains a record hash kept elsewhere, such as the
`head` of an earlier export.

**What the chain does and does not prove.** The chain and `head.json` detect accidental corruption, crashes,
stale copies and edits by programs that do not recompute hashes. They are not signed: someone who can write the
store directory can rewrite the log and `head.json` consistently. Detecting that needs a head kept outside the
store (`--expect-head`). A compaction starts a new chain, so heads anchored before it are no longer found; its
`retention` record names the head it replaced.

A store created before OV-52 has no `head.json`; it gains one at its next write. A writer from before OV-52
appends without updating it, which is accepted (the log may be ahead of the anchor, never behind it).

## Redaction (before anything is written)
- **Key rules**: values under sensitive key names (`password`, `secret`, `token`, `api_key`, `authorization`,
  `cookie`, `credential`, …) are replaced with `[REDACTED:key]`. Single-word names match whole key segments,
  so `tokens_used` is kept.
- **Pattern rules**: private keys and keys/tokens for OpenAI, Anthropic, GitHub, AWS, Slack, Telegram, Google
  and Stripe, plus JWTs, bearer tokens and credentials in URLs. Each becomes `[REDACTED:<rule>]`.
- **Protected contract fields** (ids, hashes, timestamps, statuses, versions) are never touched, and the result
  is re-validated as Contract v1.
- The policy can add key names and patterns. `pseudonymize_actor_ids` replaces actor ids with a stable keyed
  HMAC (`anon:<24 hex>`).

## Retention
`keep_days` per kind (`receipt`, `event`, `artifact`): `null` keeps forever. `blux-system retention --apply`
compacts and prunes orphaned blobs. Removing receipts can orphan children. The graph then shows the removed
parent as a `missing` placeholder.

## Export / import
`export` writes a JSONL bundle: a header (`records`, `head`) followed by the records verbatim. `import`
verifies the whole chain, the header and every record's payload, then checks every receipt and artifact against
the store and against the rest of the bundle, all before writing anything; then it ingests through the normal
(idempotent) paths. A tampered or malformed bundle → `IMPORT_INVALID`; a conflict → `RECEIPT_CONFLICT` or
`ALREADY_FINISHED`, with nothing imported (`details.imported_before_failure`). Only a writer racing the import
can make it stop part-way, and the error then says what was imported.

## Graph
`build_graph(store, root_operation_id=?, session_id=?)` returns
`{format: blux-system.graph, version: 1, roots, nodes, edges, cycles}`. Nodes carry `own_status`,
`aggregate_status`, `error_code`, timing, `artifact_ids`, `cancelled` and `missing`.
- A parent that is referenced but unknown becomes a `missing` placeholder with no status.
- Cycles are reported in `cycles` and their members listed as roots.
