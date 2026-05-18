<div align="center">

<img src="assets/logo.png" alt="flycanon" width="380" />

### **Async ingest jobs**

</div>

---

`POST /api/v1/sources` is synchronous: it blocks the request until the
binary is normalised, chunked, embedded, and indexed. That's fine for
small documents; for 50 MB PDFs or bulk uploads it blocks the
front-end and risks gateway timeouts.

The async-ingest path solves this by handing the bytes off to a
background worker and returning a job id the caller can stream for
progress.

## Lifecycle

```
caller --POST /api/v1/sources:async--> 202 { job_id }
                                       |
                                       v
                          canon_ingest_jobs row inserted
                          canon_ingest_job_events: queued
                                       |
                                       v
                          IngestRequested event published
                                       |
                                       v
                          AsyncIngestService worker picks it up
                                       |
       canon_ingest_job_events: running ----> ... ----> completed | failed
                                       |
caller <--SSE on /api/v1/jobs/{id}/stream-- frames as they're written
```

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/sources:async` | Enqueue. Same body as `/api/v1/sources` (multipart bytes, JSON+base64, or `url`). Returns `{ job_id, status: "queued" }`. |
| `GET`  | `/api/v1/jobs/{id}` | Job header -- status, progress counters, error code/message. |
| `GET`  | `/api/v1/jobs/{id}/stream` | Server-Sent Events feed of job events. Supports `?cursor=N` for resume. |
| `POST` | `/api/v1/jobs/{id}:cancel` | Co-operative cancellation; the worker checks the cancel flag between stages. |

## SSE frame format

Each event is a `canon_ingest_job_events` row serialised as an SSE
frame:

```
event: stage
data: {"cursor": 3, "stage": "embedding", "progress": 0.42}

event: stage
data: {"cursor": 4, "stage": "indexing", "progress": 0.78}

event: completed
data: {"cursor": 5, "source_id": "...", "n_chunks": 187}
```

`cursor` is monotonic; reconnect with `?cursor=5` to skip events the
client has already processed. The connection closes after `completed`
or `failed`.

## Status values

| Status | Meaning |
|--------|---------|
| `queued`    | Row created; `IngestRequested` not yet picked up. |
| `running`   | A worker has the job; events stream in. |
| `completed` | Source ingested successfully. `source_id` populated. |
| `failed`    | Pipeline raised an exception. `error_code` + `error_message` populated. |
| `cancelled` | Caller asked to stop; worker honoured at the next checkpoint. |

## Persistence

* `canon_ingest_jobs` -- one row per job. Carries the final source id
  on completion + the error code/message on failure.
* `canon_ingest_job_events` -- one row per progress frame. Indexed
  by `(job_id, cursor)` so the SSE controller can serve resumes via
  a single seek.

## When to use which path

| Path | Use when |
|------|----------|
| `POST /api/v1/sources` | Small payloads (<5 MB), interactive flows where the caller wants the row id in the response. |
| `POST /api/v1/sources:bulk` | Many small payloads in one request; you want per-item results but a synchronous response is fine. |
| `POST /api/v1/sources:async` | Large payloads, batch jobs, anywhere the caller wants progress visibility. |
