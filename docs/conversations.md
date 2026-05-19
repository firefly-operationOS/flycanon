<div align="center">

<img src="assets/logo.png" alt="flycanon" width="380" />

### **Conversations**

</div>

---

flycanon's primary surface is single-shot Q&A (`POST /api/v1/query`).
The conversation surface adds multi-turn continuity for chat-like UX
without giving up the canonical-citation guarantee.

A **conversation** is a long-lived thread; a **turn** is one user
message + the assistant's grounded answer. Every turn carries the
same `citations[]` shape as the single-shot `/query` response -- the
front end can render the same source pills with no special case for
chat.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/conversations` | Start a thread. Body: `CreateConversationRequest` (`title`, `actor`, `model`, `metadata`). Returns `{ id, title, summary: null, turns: [] }`. |
| `GET`  | `/api/v1/conversations/{id}` | Fetch header + derived rolling summary + every turn. |
| `POST` | `/api/v1/conversations/{id}/turn` | Submit a user turn. Body: `CreateTurnRequest` (`question`, `top_k`, `instructions`). Returns `{ conversation_id, turn }`. |

## Context strategy

Each turn the conversation service feeds the answer agent two layers
of context:

1. The **rolling summary** -- one line per prior turn, capped at 16
   lines (the oldest line drops off when the cap is reached). Rides
   on the system-instructions slot via `_build_steering`. Derived
   on demand from `canon_conversation_turns` rows; see
   [Race-free summary](#race-free-summary).
2. The **last 2 turns** verbatim, forwarded through pydantic-ai's
   native `message_history` slot as alternating user / assistant
   messages. Better continuity on Claude / GPT than flattening
   everything into the system prompt.

The current question is then embedded into the same hybrid retriever
the single-shot `/query` path uses. The prompt stays bounded
regardless of how long the thread runs.

## Race-free summary

`canon_conversations.summary` was once a cached column updated after
every `append_turn`. Two parallel `POST /turn` calls would both read
the same stale string, both compute next-summary differing by one
line, and the second update would clobber the first's line.

The summary is now a **pure function of `canon_conversation_turns`
ordered by `turn_index`**:

```python
ConversationService._summary_from_turns(turns)
```

Two concurrent turn appends correctly serialise via
`UNIQUE(conversation_id, turn_index)` (with a bounded retry loop
inside `append_turn`). Once both turn rows are committed, any reader
sees both lines in the derived summary — there's nothing left to
race on. See [concurrency.md § Conversation rolling summary](concurrency.md#conversation-rolling-summary)
for the architectural rationale.

The `summary` column on `canon_conversations` is vestigial; a
follow-up migration drops it.

## Persistence

* `canon_conversations` -- one row per thread. `title`, `actor`,
  `model`, `metadata_json`, `created_at`, `updated_at`. The
  `summary` column is no longer written to.
* `canon_conversation_turns` -- one row per turn. `UNIQUE(conversation_id,
  turn_index)` is the concurrency anchor. Carries the question,
  answer, citations, model, latency, and `no_answer` flag.

Both tables ride the same audit + EDA rules as the rest of the
service.

## Streaming

For long answers, the dedicated `POST /api/v1/query/stream` endpoint
streams tokens as Server-Sent Events. The frame format mirrors
[async-ingest.md](async-ingest.md):

```
event: token
data: {"text": "Documents must be retained "}

event: token
data: {"text": "for at least seven years"}

event: complete
data: {"answer": "...", "citations": [...]}
```

The conversation controller itself does not stream today -- the
assistant answer rides the `POST /turn` response body so the
citation contract is unambiguous. Streaming for chat is on the
roadmap.

## Suggested follow-ups

`POST /api/v1/query/suggest` (note: the suggestion endpoint lives on
the query surface, not the conversation surface) produces 3-5
follow-up questions grounded in the answer + citations. Useful for
UI affordances ("you might also ask..."). The response shape is
deliberately tiny:

```json
{ "questions": ["...", "..."] }
```
