# Receipt store (format 1)

## Layout
```
<store>/
  store.json        {"format": "blux-system-store", "version": 1, "created_at": ...}
  retention.json    blux-system.retention v1 policy
  log.jsonl         append-only, hash-chained records
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
  cannot fork the chain or race a conflict check.
- A receipt is immutable:
  - an identical re-ingest → `duplicate` (no-op);
  - different content under the same `receipt_id`, or a second receipt for the same `operation_id` → `RECEIPT_CONFLICT`.
- Cancellation is allowed only before a receipt exists. A receipt for a cancelled operation → `ALREADY_FINISHED`.
- **Retention compaction is the only rewrite.** It is explicit, writes a new chain atomically
  (`os.replace`), and appends a `retention` record naming what was removed.

## Corruption and failure handling
| Condition | Behaviour |
|---|---|
| Unterminated last line (crash mid-append) | A writable open truncates exactly that line and appends a `recovery` record. A read-only open serves the intact prefix |
| Edited record, deleted/reordered line, garbage, re-hashed forgery | `STORE_CORRUPT` with `{line, seq, reason}`: `hash_mismatch`, `sequence_gap`, `broken_chain` or `unparseable`. Nothing is repaired automatically |
| Log changed by another program between writes | Detected before the next append (`STORE_CORRUPT`) |
| Bad `store.json` | `STORE_CORRUPT` |
| Concurrent writer | `STORE_LOCKED` |

`blux-system verify` runs read-only and reports the first problem.

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
verifies the whole chain and header before writing anything, then ingests through the normal (idempotent)
paths. A tampered bundle → `IMPORT_INVALID`.

## Graph
`build_graph(store, root_operation_id=?, session_id=?)` returns
`{format: blux-system.graph, version: 1, roots, nodes, edges, cycles}`. Nodes carry `own_status`,
`aggregate_status`, `error_code`, timing, `artifact_ids`, `cancelled` and `missing`.
- A parent that is referenced but unknown becomes a `missing` placeholder with no status.
- Cycles are reported in `cycles` and their members listed as roots.
