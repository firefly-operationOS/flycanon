<div align="center">

<img src="assets/logo.png" alt="flycanon" width="380" />

### **Concurrency model**

</div>

---

flycanon is built to scale horizontally -- you can run N replicas of
the API, M replicas of the worker, and route in front. The model
below documents how the service stays consistent under that
deployment.

## Defence in depth

Two independent layers protect every concurrent operation:

1. **Database invariants.** Unique constraints, partial indexes, and
   `RETURNING` claim atomically — the storage layer is the final
   arbiter of correctness even when the application logic loses a
   race.
2. **Application-level idempotency.** Service code pre-checks the
   invariant and retries / short-circuits on the IntegrityError /
   null-rowcount the DB returns when the race goes the other way.
   No surfaces leak a 500 on a concurrent retry.

## Surface-by-surface

### Async ingest jobs (`canon_ingest_jobs`)

The single most-likely source of double-processing under
multi-replica deployments. Defended by `mark_running` being a single
atomic claim:

```sql
UPDATE canon_ingest_jobs
   SET status = 'running',
       attempts = attempts + 1,
       started_at = COALESCE(started_at, now())
 WHERE id = $1
   AND status = 'queued'
RETURNING *;
```

The `WHERE status = 'queued'` is the lock — only one transaction can
flip the row out of `queued`. Subsequent workers receive zero rows
back; `AsyncIngestService.process` short-circuits with a logged
"duplicate delivery skipped" line. The previous read-modify-write
pattern would let two replicas both succeed and double-ingest the
same payload.

> **EDA fan-out caveat.** The default postgres EDA adapter delivers
> every event to every replica that subscribes (one offset per
> consumer_group; if every replica picks a distinct group, they each
> get the full stream). The atomic claim above is what makes that
> safe — exactly one worker actually runs the intake pipeline; the
> rest skip immediately. Switch to the Redis / Kafka adapter if you
> want competing-consumer semantics at the broker level.

### Source intake (`canon_sources`)

`content_sha256` carries a partial-unique index. The intake service:

1. Computes the SHA-256 inside the ingestion stage.
2. **Pre-checks** `SourceRepository.get_by_content_sha256(sha)` — if
   another caller already landed the same bytes, return that row
   (idempotent).
3. INSERT. If the pre-check raced (two clients hit a window between
   the SELECT and the INSERT), the IntegrityError is caught and the
   pre-check is repeated to surface the now-committed row.

Net result: parallel POSTs of the same file resolve to the same
`SourceRecord`, no 500s.

### Conversation turns (`canon_conversation_turns`)

`(conversation_id, turn_index)` carries a UNIQUE constraint.
`ConversationService.append_turn` builds the turn row inside a
bounded retry loop:

* Recompute `next_turn_index` from the database before each attempt.
* INSERT.
* On IntegrityError (another `POST /turn` won the race), log + retry.
* Three attempts cap; rare in practice because the answer call is
  the dominant latency, so the contention window is microseconds.

### Knowledge versions (`canon_knowledge_versions`)

`(knowledge_item_id, version)` carries a UNIQUE constraint.
`KnowledgeService.update` catches the IntegrityError from
`add_version` and translates it into the typed
`KnowledgeVersionConflict` (HTTP 409, code
`knowledge_version_conflict`). Callers re-read the item, observe the
bumped `current_version`, and re-submit. This replaces the prior
behaviour where the bare IntegrityError leaked as a 500.

### Conflict-detection candidates (inbox queue)

The `ConflictDetector.detect()` LLM-driven pass is non-cheap, and
nothing stops two operators from launching it at the same time. The
detector now calls `CandidateRepository.find_conflict_candidate`
before inserting; a previously-proposed-but-not-yet-decided
candidate for the same `(from_item_id, to_item_id)` short-circuits
and the existing id is returned. The `conflicts_with` knowledge-graph
edge side is independently protected by the
`(from_item_id, to_item_id, kind)` UNIQUE constraint and was
already idempotent.

### Knowledge graph relations

`(from_item_id, to_item_id, kind)` on `canon_knowledge_relations`
carries a UNIQUE constraint. `KnowledgeRelationService.add` catches
the IntegrityError and translates it into `RelationConflictError`
(HTTP 409, code `relation_already_exists`).

### Audit log + EDA outbox

Append-only by design. No primary-key collision possible; every row
has its own monotonic id. The audit log is the durable record even
when EDA publish fails (publish errors are logged but never abort
the originating mutation).

## What's not protected (yet)

* **Knowledge supersession** -- the lifecycle pointers update on the
  item row aren't wrapped in a row-lock. Two simultaneous
  `:supersede` calls on the same item could race on the
  `superseded_by_item_id` field; last writer wins, but both audit
  rows still land. Acceptable for v1 (supersession is a low-frequency
  admin operation); a follow-up could add row-level locking.
* **Stale-score cache** -- `KnowledgeItemRow.metadata_json.staleness`
  uses last-write-wins. The values are deterministic per item +
  version, so two concurrent scans converge on the same number;
  acceptable.
* **Pluggable EDA broker selection** -- when running with the default
  postgres adapter and a non-trivial worker fleet, give each replica
  a distinct `consumer_group`. The atomic job claim above ensures
  the fan-out doesn't translate into duplicate ingest. Switching to
  Redis Streams or Kafka enables broker-level competing-consumer
  semantics if that fits your operational model better.

## Coverage

`tests/unit/test_concurrent_operations.py` exercises the atomic
claim + dedup helpers directly. The `IntegrityError` paths in
`IntakeService.submit`, `ConversationService.append_turn`, and
`KnowledgeService.update` rely on the DB-level UNIQUE constraints,
which are exercised in the live integration smoke (Docker stack)
because SQLite + in-memory pytest can't easily simulate two parallel
transactions racing inside one session.
