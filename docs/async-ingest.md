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
caller --POST /api/v1/sources?mode=async--> 201 { id, status: "queued" }
                                       |
                                       v
                          canon_ingest_jobs row inserted
                          canon_ingest_job_events: queued
                                       |
                                       v
                          IngestSourceRequested event published
                                       |
                                       v
                          AsyncIngestService.process picks up via EDA
                                       |
       canon_ingest_job_events: normalising → finished | failed
                                       |
caller <--SSE on /api/v1/ingest-jobs/{id}/stream-- frames as they're written
```

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/sources?mode=async` | Enqueue. Same `SubmitSourceJsonPayload` body as the sync POST. Optional `?callback_url=…` fires a webhook on terminal state. Returns the `IngestJob` row (`id`, `status: "queued"`). |
| `GET`  | `/api/v1/ingest-jobs` | Paginated job list. Query: `status` (csv), `limit`, `offset`. (Renamed from `/api/v1/jobs` in Plan 4.) |
| `GET`  | `/api/v1/ingest-jobs/{id}` | Job header -- `status`, `attempts`, `source_id` (on success), `error_code` / `error_message` (on failure), `started_at`, `finished_at`. |
| `GET`  | `/api/v1/ingest-jobs/{id}/stream` | Server-Sent Events feed of progress events. Resume with `?after_id=N`. |

## Status values

| Status | Meaning |
|--------|---------|
| `queued`    | Row created; worker hasn't claimed yet. |
| `running`   | A worker holds the lease; events stream in. |
| `succeeded` | Intake completed. `source_id` populated, `finished_at` set. |
| `failed`    | Pipeline raised an exception. `error_code` + `error_message` populated. |

## SSE frame format

Each event is a `canon_ingest_job_events` row serialised as an SSE
frame:

```
event: stage
data: {"id": 3, "stage": "normalising", "message": "binary normalise + load"}

event: stage
data: {"id": 4, "stage": "finished", "payload": {"source_id": "...", "n_chunks": 187}}
```

`id` is monotonic; reconnect with `?after_id=4` to skip events the
client has already processed.

## Concurrency safety

The async-ingest path is the single most-likely source of
double-processing under multi-replica deployments — the default
postgres EDA adapter delivers every event to every replica that
subscribes. Three layers of defence:

1. **Atomic claim**. `IngestJobRepository.mark_running` is a single
   `UPDATE … WHERE id=$1 AND (status='queued' OR stale-running)
   RETURNING`. Exactly one worker can flip the row out of `queued`;
   subsequent workers receive `None` and `AsyncIngestService.process`
   short-circuits with a "duplicate delivery skipped" log line.
2. **Stuck-job recovery**. A `running` row whose `started_at` is older
   than `FLYCANON_INGEST_TIMEOUT_S` (default 600s) is re-claimable
   by the next worker. Handles the worker-crashed-mid-run case.
3. **Periodic sweep**. `AsyncIngestService.sweep_stuck_jobs` runs
   every 60 s via pyfly's `@scheduled` and republishes
   `IngestSourceRequested` for any stale `running` row that the
   in-band re-claim hasn't picked up (e.g. the broker also dropped
   the redelivery). Covers the worst-case "worker crashed AND broker
   lost message" scenario without operator intervention.
4. **Lease-poaching guard**. `mark_succeeded` and `mark_failed`
   gate their UPDATE on `attempts` equalling the value the worker
   captured at claim time. If another replica re-claimed the job
   mid-run, the late worker's commit returns `None` and it bows out
   without double-publishing the terminal event.
5. **Poison-job guard**. After
   `FLYCANON_INGEST_MAX_ATTEMPTS` (default 3) reclaims, the next
   worker marks the row `failed` with `code=attempts_exhausted` so
   a broken payload can't eat replica budget forever.

See [concurrency.md § Async ingest jobs](concurrency.md#async-ingest-jobs-canon_ingest_jobs)
for the SQL + the rationale behind each defence.

## Persistence

* `canon_ingest_jobs` -- one row per job. Carries `attempts`,
  `started_at`, `source_id` on success, `error_code` /
  `error_message` on failure, optional `callback_url`.
* `canon_ingest_job_events` -- one row per progress frame. Indexed
  by `(job_id, id)` so the SSE controller serves resumes with a
  single seek.

## When to use which path

| Path | Use when |
|------|----------|
| `POST /api/v1/sources` (sync) | Small payloads (<5 MB), interactive flows where the caller wants the row id in the response. |
| `POST /api/v1/sources:bulk` | Many small payloads in one request; you want per-item results but a synchronous response is fine. |
| `POST /api/v1/sources?mode=async` | Large payloads, batch jobs, anywhere the caller wants progress visibility via SSE. |
