<div align="center">

<img src="../../docs/assets/logo.png" alt="flycanon" width="380" />

### **Python SDK quickstart**

</div>

---

Five-minute tour of the async Python client. Assumes the flycanon
service is running on `http://localhost:8500` (see the service-level
[QUICKSTART](https://github.com/firefly-operationOS/flycanon/blob/main/QUICKSTART.md)).

## 1. Install

```bash
uv add flycanon-sdk
```

## 2. Ingest a source

```python
import asyncio
import base64
from pathlib import Path

from flycanon_sdk import CanonClient, SourceMetadata, SubmitSourceJsonPayload

async def ingest() -> None:
    async with CanonClient(base_url="http://localhost:8500") as client:
        payload = SubmitSourceJsonPayload(
            content_base64=base64.b64encode(Path("sample.docx").read_bytes()).decode(),
            filename="sample.docx",
            content_type=(
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            ),
            metadata=SourceMetadata(title="Sample", domain="process"),
        )
        source = await client.submit_source(payload)
        print(source.model_dump_json(indent=2))

asyncio.run(ingest())
```

## 3. Search the corpus

Every hit carries the rich source-side context (filename, title,
kind, section breadcrumb, page) at the top level — no second
`get_source(...)` round-trip needed to render a citation label.

```python
async def search() -> None:
    async with CanonClient(base_url="http://localhost:8500") as client:
        result = await client.search("what does the document say about scope?", top_k=5)
        for hit in result.hits:
            label = hit.source_filename or hit.source_title or hit.source_id
            location = hit.section_path or ""
            if hit.page:
                location = f"{location} (page {hit.page})" if location else f"page {hit.page}"
            print(f"{hit.score:.4f}  [{hit.source_kind}] {label} -- {location}")
            print(f"        {hit.content[:120]}")

asyncio.run(search())
```

## 4. Ask a question

```python
async def ask() -> None:
    async with CanonClient(base_url="http://localhost:8500") as client:
        answer = await client.answer("Summarise the scope section in three sentences.")
        print(answer.answer)
        for citation in answer.citations:
            label = citation.source_filename or citation.source_title or citation.source_id
            print(f"  - {label} ({citation.source_kind})"
                  f" -- section: {citation.section_path or '-'}"
                  f" -- score: {citation.score:.3f}")

asyncio.run(ask())
```

## 5. Browse the canonical layer

```python
async def browse() -> None:
    async with CanonClient(base_url="http://localhost:8500") as client:
        items = await client.list_knowledge_items(domain=["process"])
        for item in items.items:
            print(item.title, "current_version=", item.current_version)

asyncio.run(browse())
```

## Error handling

`CanonClient` raises `CanonAPIError` on any non-2xx response. The
exception carries the service's RFC 7807 ProblemDetails payload --
including the stable `code` SDKs branch on:

```python
from flycanon_sdk import CanonAPIError

try:
    await client.get_knowledge("00000000-0000-0000-0000-000000000000")
except CanonAPIError as exc:
    if exc.code == "knowledge_item_not_found":
        ...  # graceful 404 handling
```
