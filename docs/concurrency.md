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
       started_at = now(),
       error_code = NULL,
       error_message = NULL
 WHERE id = $1
   AND ( status = 'queued'
      OR (status = 'running' AND started_at < now() - lease) )
RETURNING *;
```

The `WHERE` clause is the lock — only one transaction can flip the
row out of `queued`. Subsequent workers receive zero rows back;
`AsyncIngestService.process` short-circuits with a logged "duplicate
delivery skipped" line. The previous read-modify-write pattern would
let two replicas both succeed and double-ingest the same payload.

**Stuck-job recovery.** The second branch on the `WHERE` clause
re-claims `running` rows whose lease (`FLYCANON_INGEST_TIMEOUT_S`,
default 600s) has expired. That handles the worker-crash-mid-run
case: without recovery a row sitting at `running` would be
unreachable forever because the next worker's claim would never
match. `IngestJobRepository.reclaim_stuck` is the matching bulk
sweep for rows whose EDA delivery was also lost. A poison-job guard
in `AsyncIngestService.process` aborts after
`FLYCANON_INGEST_MAX_ATTEMPTS` reclaims so a broken payload can't
eat replica budget forever.

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

### Candidate accept / reject

`CandidateService.accept` and `reject` used to do check-then-act on
`status == 'proposed'`. Two operators clicking accept in the inbox
both passed the gate, both wrote a knowledge item, then the second
`candidate.update` silently overwrote the first. The fix splits the
decision into two atomic steps:

1. `CandidateRepository.claim_decision` — single-statement
   `UPDATE … WHERE id=$1 AND status='proposed' RETURNING` that flips
   status to `accepted` / `rejected`. The loser observes `None` and
   the service raises `CandidateAlreadyDecided` (HTTP 409).
2. The knowledge-item write (create or update) runs only after the
   claim is held. `CandidateRepository.finalise` then attaches the
   materialised pointers in a second atomic UPDATE.

### Knowledge graph relations

`(from_item_id, to_item_id, kind)` on `canon_knowledge_relations`
carries a UNIQUE constraint. `KnowledgeRelationService.add` catches
the `IntegrityError` (the typed SQLAlchemy exception, not a substring
match on the engine's text) and translates it into
`RelationConflictError` (HTTP 409, code `relation_already_exists`).

### Knowledge supersede / retire

Lifecycle transitions on `canon_knowledge_items.status` are now
atomic. `KnowledgeRepository.claim_status_transition` does a single
`UPDATE … WHERE id=$1 AND status IN (allowed) RETURNING` that flips
the status and, in the same statement, updates the matching
pointers (`superseded_by_item_id` for supersede;
`retired_at` + `retired_reason` for retire). The loser branch returns
`None`, surfaced as a typed 409.

Two simultaneous `:supersede` calls used to race on
`superseded_by_item_id` (last writer wins on the field). They now
serialise cleanly: one operator's pointer survives; the other gets
a typed `InvalidSupersedeTarget`. Retire is similar — concurrent
`:retire` calls no longer double-publish lifecycle events.

### Conversation rolling summary

`ConversationService.append_turn` used to read the conversation row
once at the top of the function, append the turn (correctly
serialised via `UNIQUE(conversation_id, turn_index)` + retry), and
then write the next summary computed from the stale captured row.
Two concurrent turns would lose one summary line in the process.
The summary is now recomputed from a fresh `repository.get` after
the turn insert, so each call appends one line cleanly. The audit
log and turn rows are unaffected — both already serialise via
their own constraints.

### Audit log + EDA outbox

Append-only by design. No primary-key collision possible; every row
has its own monotonic id. The audit log is the durable record even
when EDA publish fails (publish errors are logged but never abort
the originating mutation).

## What's not protected (yet)

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
