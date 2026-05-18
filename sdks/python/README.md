<div align="center">

<img src="../../docs/assets/logo.png" alt="flycanon" width="380" />

### **Python SDK**

</div>

---

Async-first Python client for the
[flycanon](https://github.com/firefly-operationOS/flycanon) Operational
Knowledge Repository service.

* Pydantic-typed request + response models.
* httpx under the hood -- pooled connections, retries, timeouts you
  control.
* No service dependency -- the SDK ships its own Pydantic schemas so
  it can be installed alongside any client codebase without pulling
  the framework.

## Install

```bash
uv add flycanon-sdk
# or
pip install flycanon-sdk
```

## Usage

```python
import asyncio
from flycanon_sdk import CanonClient, SubmitSourceJsonPayload, SourceMetadata
from pathlib import Path
import base64

async def main():
    async with CanonClient(base_url="http://localhost:8500") as client:
        bytes_ = Path("./sample.docx").read_bytes()
        source = await client.submit_source(
            SubmitSourceJsonPayload(
                content_base64=base64.b64encode(bytes_).decode(),
                filename="sample.docx",
                content_type=(
                    "application/vnd.openxmlformats-officedocument."
                    "wordprocessingml.document"
                ),
                metadata=SourceMetadata(title="Sample", domain="process"),
            )
        )
        print("ingested:", source.id, source.n_chunks, "chunks")

        answer = await client.answer("Summarise the scope section.")
        print(answer.answer)
        for citation in answer.citations:
            print(" -", citation.source_id, citation.score)

asyncio.run(main())
```

See [QUICKSTART.md](QUICKSTART.md) for the full five-minute tour.

## What the client covers

Every public flycanon endpoint has a typed async method on
`CanonClient`. Highlights:

```python
# Bulk + async + replace intake
await client.submit_sources_bulk([payload_a, payload_b])
job = await client.submit_source_async(payload)
async for event in client.stream_job(job.id):
    print(event.event, event.data)
await client.replace_source(source_id, new_payload)

# Knowledge graph + diff
diff = await client.get_diff(item_id, from_version=1, to_version=2)
relations = await client.list_relations(item_id)
await client.add_relation(item_id, CreateRelationRequest(
    to_item_id=other_id, kind="depends_on",
))
graph = await client.get_graph(domain="compliance")
mermaid_str = await client.get_graph_mermaid(domain="compliance")

# Conversations -- pydantic-ai message_history wired underneath
conv = await client.create_conversation(CreateConversationRequest(title="t"))
turn = await client.add_turn(conv.id, CreateConversationTurnRequest(
    query="What about scope?",
))
suggestions = await client.suggest_questions(conv.id)

# Streamed answer (token-by-token SSE)
async for frame in client.stream_answer("Summarise the scope section."):
    if frame.event == "token":
        print(frame.data.get("text"), end="", flush=True)

# Quality scans
stale = await client.scan_stale()
report = await client.detect_conflicts(ConflictScanRequest(
    domain="compliance", min_similarity=0.85,
))

# Billing + corpus inventory
summary = await client.billing_summary()
top = await client.billing_top(dimension="model", limit=5)
latency = await client.billing_latency(group_by=["model"])
snapshot = await client.stats()
```

Every method returns a Pydantic model (or a typed `AsyncIterator` for
the SSE streams) -- IDE autocomplete works out of the box.

## License

Apache-2.0 -- see [LICENSE](LICENSE).
