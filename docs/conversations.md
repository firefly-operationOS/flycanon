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
| `POST` | `/api/v1/conversations` | Start a thread. Optional `title`, `actor`. Returns `{ id, title, summary, turns: [] }`. |
| `GET`  | `/api/v1/conversations/{id}` | Fetch header + rolling `summary` + last N turns. |
| `POST` | `/api/v1/conversations/{id}/turns` | Submit a user turn. Body: `{ query, max_chunks?, hybrid_mode? }`. Returns the assistant answer + citations + turn id. |
| `GET`  | `/api/v1/conversations/{id}/turns` | Paginated turn history (oldest first). |
| `POST` | `/api/v1/conversations/{id}/suggest` | Returns 3-5 short follow-up questions grounded in the thread so far. |

## Context strategy

Each turn we feed the answer agent:

1. The **rolling summary** (~250 tokens; the conversation service
   refreshes it every N turns by summarising the older portion).
2. The **last 2 turns** verbatim (so anaphora resolves -- "what about
   the legal one?" still anchors to "the legal team's data-retention
   policy" from a moment ago).
3. The current query embedded into the same hybrid retriever the
   single-shot path uses.

This keeps the prompt bounded regardless of how long the thread runs.

## Persistence

* `canon_conversations` -- one row per thread; carries the rolling
  summary.
* `canon_conversation_turns` -- one row per turn; carries the user
  query, the assistant body, and the citation set.

Both tables are subject to the same audit + EDA rules as the rest of
the service.

## Streaming

For long answers, the `POST /api/v1/query:stream` endpoint streams the
tokens as Server-Sent Events. The frame format mirrors
[async-ingest.md](async-ingest.md):

```
event: token
data: {"text": "Documents must be retained "}

event: token
data: {"text": "for at least seven years"}

event: complete
data: {"answer": "...", "citations": [...]}
```

The conversation controller does not stream today -- the assistant
answer comes back in the `POST /turns` response body so the citation
contract is unambiguous. Streaming for chat is on the roadmap.

## Suggested follow-ups

`POST /api/v1/conversations/{id}/suggest` runs a short, low-temperature
LLM call that produces 3-5 follow-ups based on the rolling summary +
last few turns. Useful for UI affordances ("you might also ask..."),
and the response shape is deliberately tiny:

```json
{ "questions": ["...", "..."] }
```
