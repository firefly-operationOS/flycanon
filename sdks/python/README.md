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

## License

Apache-2.0 -- see [LICENSE](LICENSE).
