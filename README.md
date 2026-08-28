# Ontology-KnowledgeGraph-Data-Visualization
Turn your data into a queryable knowledge graph — no graph expertise needed. Powered by LLMs for ontology inference and natural language Q&A.

## Table of Contents

- [Data](#data)
  - [Supported Input Formats](#supported-input-formats)
  - [Upload Requirements](#upload-requirements)
  - [Data Validation](#data-validation)
- [Processing Architecture](#processing-architecture)
  - [Service Design](#service-design)
  - [Message Queue — Why RabbitMQ](#message-queue--why-rabbitmq)
  - [Design Patterns](#design-patterns)
  - [Scaling Considerations](#scaling-considerations)

---

## Data

### Supported Input Formats

| Format | Extensions | Notes |
|--------|------------|-------|
| CSV / TSV | `.csv`, `.tsv` | Header row required |
| JSON | `.json` | Flat array and nested objects supported |
| SQL Dump | `.sql` | DDL + DML |
| Parquet | `.parquet` | |
| Excel | `.xlsx` | Planned — not yet implemented |

---

### Upload Requirements

Files must satisfy the following conditions before being accepted:

| Condition | Limit |
|-----------|-------|
| Max file size | 20 MB |
| Min file size | > 0 bytes |

Files are also checked for exact duplicates via SHA-256 hash.

---

### Data Validation

The following aspects are validated during processing:

- **Encoding** — UTF-8 required
- **Structure** — header presence, column count consistency, delimiter detection (CSV)
- **Content** — null/missing values, duplicate rows, type consistency
- **Graph readiness** — identifiable primary keys, detectable relationships between entities

---

## Processing Architecture

### Service Design

The system is split into two independent services communicating via RabbitMQ:

```
┌─────────────────┐         ┌──────────────┐         ┌─────────────────┐
│   API Service   │ publish │   RabbitMQ   │ consume │  Worker Service │
│  (Producer)     │ ──────► │              │ ──────► │  (Consumer)     │
│                 │         │  job_queue   │         │                 │
│ • Handle upload │         │  dead_queue  │         │ • Parse files   │
│ • Validate      │         │              │         │ • Normalize     │
│ • Write DB      │         └──────────────┘         │ • Update DB     │
│ • Return status │                                  │                 │
└─────────────────┘                                  └─────────────────┘
         │                                                    │
         └──────────────────┬─────────────────────────────────┘
                            ▼
                       PostgreSQL
```

Splitting into two services ensures that a slow or crashing Worker does not affect the API's ability to accept uploads. Jobs remain in the queue and are processed once the Worker recovers.

---

### Message Queue — Why RabbitMQ

Three options were considered: PostgreSQL LISTEN/NOTIFY, RabbitMQ, and Kafka.

**PostgreSQL LISTEN/NOTIFY** was ruled out because it is not a real message broker — it lacks built-in retry, dead-lettering, and backpressure, which are required for resilient job processing.

**Kafka** was ruled out because it is designed for high-throughput event streaming, not task queues. Its setup complexity (ZooKeeper or KRaft) and resource footprint are not justified for this use case.

**RabbitMQ** was chosen for the following reasons:

| Reason | Detail |
|--------|--------|
| Correct pattern fit | Designed for task queue workloads — exactly this use case |
| Built-in Dead Letter Exchange | DLQ is native, no custom implementation needed |
| Per-message acknowledgment | Supports idempotency and safe retry |
| Lightweight | Easy to run locally via Docker |
| Flexible routing | Exchanges support future Bulkhead pattern (separate queues per format) |

**Tradeoffs accepted:**

| Tradeoff | Detail |
|----------|--------|
| Not an event log | A classic queue deletes each message on acknowledgment, so the queue is not a record of what ran |
| No replay from the queue | An acknowledged message cannot be re-read — there is no offset to rewind to |
| Lower throughput | Tens of thousands of messages per second, against millions for Kafka |
| Harder to scale horizontally | Kafka shards a topic into partitions and gains parallelism by adding them; a RabbitMQ queue is one logical unit, scaled by adding competing consumers or by splitting into several queues by hand |

**How this project works around them**

History and replay come from PostgreSQL rather than from the broker. The `jobs` table records every job with its status, attempt count, error, and timestamps, so auditing is a query against that table and replaying is selecting rows from it and publishing them again — safe because the Worker's idempotency check makes a repeated message a no-op. This is a mechanism built here, not something RabbitMQ provides.

Its limit is that `jobs` holds current state, not event-level history: an earlier attempt's error is overwritten by the next. Should that history be needed, RabbitMQ **stream queues** (`x-queue-type: stream`) are append-only logs with non-destructive reads and age- or size-based retention, and would run alongside the classic job queue rather than replace it. Note that `x-message-ttl` is not equivalent — it bounds how long an *unconsumed* message may wait before being discarded, whereas log retention guarantees availability *after* reading.

The remaining two tradeoffs are not worked around because they are not reached: the workload is a handful of 20MB uploads, and per-format queues (Bulkhead) cover the foreseeable scaling need.

---

### Design Patterns

#### Producer-Consumer

The API Service acts as the Producer — it publishes a job to RabbitMQ after a successful upload. The Worker Service acts as the Consumer — it pulls jobs from the queue and processes them.

**Why:** Decouples upload from processing. The API responds immediately without waiting for file parsing to complete.

**Tradeoff:** Introduces eventual consistency — the client must poll or be notified asynchronously rather than receiving a result inline.

---

#### Idempotency

Before processing a job, the Worker checks whether `job_id` already has `status = done` in PostgreSQL. If so, it acknowledges the message and skips processing.

**Why:** Retry logic can cause the same job to be delivered more than once. Without idempotency, this would produce duplicate normalized data.

**Tradeoff:** Adds one DB lookup per job. Negligible in practice but worth noting if job volume grows very large.

---

#### Dead Letter Queue (DLQ)

If a job fails after 3 attempts, it is published to a separate `dead_queue` in RabbitMQ and marked `status = failed` in PostgreSQL.

**Why:** Prevents bad jobs from blocking the main queue indefinitely. Failed jobs are preserved for investigation and potential manual re-processing.

**Tradeoff:** Failed jobs require manual intervention — there is no automatic recovery. A monitoring process for the dead queue should be added when operating at scale.

---

### Scaling Considerations

The current design is intentionally simple for local MVP use. The following changes would be made when scaling:

| Concern | MVP | At Scale |
|---------|-----|----------|
| Worker concurrency | Single worker instance | Multiple Worker instances consuming from the same queue |
| Queue isolation | Single job queue | Separate queues per file format (Bulkhead pattern) — slow SQL parsing does not delay CSV parsing |
| Message replay / audit | Served by the `jobs` table | Add a RabbitMQ stream queue alongside the job queue for event-level history — no change of broker |
| Job persistence | In-flight jobs lost on Worker restart (re-queued via RabbitMQ) | Outbox pattern ensures no job is lost even if both API and queue are temporarily unavailable |
| Backpressure | Queue grows unbounded | Cap queue size, return HTTP 429 when limit reached |
| File storage | Local disk | AWS S3 or Azure Blob Storage (see below) |

---

### File Storage at Scale

In the local MVP, uploaded files are stored on the local filesystem. When deployed to a cloud environment, file storage should be migrated to an object store:

- **AWS S3** — store raw uploaded files under a structured key prefix (e.g., `uploads/{session_id}/{filename}`)
- **Azure Blob Storage** — equivalent option depending on cloud provider

Both the API Service (write) and Worker Service (read) reference files via their storage path recorded in the `File` table. Switching to object storage requires only updating the read/write layer — no changes to the queue or processing logic.

---

### RabbitMQ Failure Handling

An outage affects the two services differently.

The **Worker** copes on its own. `connect_robust` retries with backoff, and RabbitMQ keeps durable messages through a restart, so the Worker pauses and then resumes where it left off. Nothing is required here.

The **API** is the exposed side. Its upload writes rows to PostgreSQL and then publishes a job to RabbitMQ — two systems, no transaction spanning both. This is the **dual-write problem**: if the commit succeeds and the publish does not, the database records a job that nothing will ever run.

That failure is observable today. When the broker was stopped mid-test, an upload committed its rows, failed to publish, and returned `503`. The session was left `queued` with no message behind it — and because a `queued` session cannot be deleted, and its content hash now blocks re-uploading the same file, that session was stuck with no way forward or back.

#### Solution: Transactional Outbox *(analysed, not implemented in this version)*

> Deferred deliberately. At MVP scale an outage is noticed and dealt with by hand, and the design below is documented so it can be added without rework rather than discovered under pressure.

The fix is not to make two writes more reliable but to reduce them to one: record the intent to publish inside the same transaction as the business data, and let a separate relay deliver it.

```
┌─ BEGIN ─────────────────────────────┐
│  INSERT sessions                    │
│  INSERT files                       │   one transaction, one system —
│  INSERT job  (status: queued)       │   all of it commits, or none
└─ COMMIT ────────────────────────────┘
                  │
                  ▼
   Relay: publish queued jobs → mark dispatched
```

**The `jobs` table already is this outbox.** A row with `status = queued` and no message in flight is exactly an undelivered outbox entry, so no new table is needed — only the relay that drains it, and a `published_at` column to tell "waiting to be sent" from "sent, waiting to be run".

This gives at-least-once delivery: a relay that crashes between publishing and marking will publish again. That is safe here because the Worker skips a job it has already completed.

**What it changes for callers.** An upload no longer fails when the broker is down. The work is durably recorded, so the API can answer `201` and let the relay deliver the job whenever RabbitMQ returns. A broker outage stops being an error the user sees and becomes a delay they do not notice.

#### Waking the relay

| Mechanism | Latency | Trade-off |
|-----------|---------|-----------|
| Polling on an interval | One interval | Simplest; costs a periodic query that usually finds nothing |
| PostgreSQL `LISTEN/NOTIFY` | Near-immediate | Still needs polling as a backstop in case a notification is missed |
| Change data capture (Debezium) | Very low | Reads the write-ahead log directly, but brings Kafka Connect with it — worth it only where that already exists |

#### The reconciliation sweep

Whichever mechanism dispatches jobs, a periodic sweep is what makes the system self-healing:

```sql
SELECT job_id, session_id FROM jobs
WHERE status = 'queued'
  AND queued_at < now() - interval '5 minutes'
```

Re-publishing these is safe for the same reason the relay is. This is the piece that recovers from cases nobody anticipated, including a bug in the relay itself, and it is the smallest useful step: it needs no schema change and removes the stuck state described above.

Worth watching alongside it: the number of jobs sitting in `queued`, and the depth of the dead-letter queue. Either one climbing is the earliest sign that dispatch has stalled.

#### What this does not solve

An outbox stops jobs from being lost; it does not keep the broker up. Reducing outages themselves is a separate concern — a RabbitMQ cluster with quorum queues survives losing a node — and clustering does not remove the need for an outbox, because the dual write remains.

Two approaches are sometimes suggested and rejected here. **Two-phase commit** across the database and broker is technically possible but performs poorly, blocks on a coordinator failure, and is barely supported by client libraries. **Publishing before writing to the database** only works when the message carries enough to rebuild the state; ours carries identifiers that refer back to rows, so it does not.
