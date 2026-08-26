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

If RabbitMQ becomes unavailable, two problems arise simultaneously:

- **API Service (Producer):** Cannot publish jobs → uploads succeed but jobs are silently lost
- **Worker Service (Consumer):** Loses connection → processing stops entirely

#### Solution: Outbox Pattern *(evaluated, not implemented in this version)*

> The Outbox Pattern was considered as a solution for RabbitMQ failure resilience. While it provides strong guarantees around job durability, it adds significant implementation complexity and is deferred from the current scope. The analysis is documented here for future reference.

To prevent job loss when RabbitMQ is down, the API Service writes the job to PostgreSQL first before publishing:

```
API Service:
  INSERT Job (status: pending)        ← atomic with file record
  Publish to RabbitMQ
    Success → UPDATE Job (status: queued)
    Fail    → job stays as pending in DB

Outbox Poller (background process in API Service):
  SELECT Jobs WHERE status = 'pending'
  Retry publish to RabbitMQ
    Success → UPDATE Job (status: queued)
```

When RabbitMQ recovers, the Outbox Poller automatically flushes all pending jobs — no jobs are lost regardless of how long the outage lasts.

#### Worker Reconnection

The Worker Service implements connection retry with exponential backoff. When RabbitMQ recovers, the Worker reconnects automatically and resumes consuming from where it left off. RabbitMQ retains all durable messages during the outage.

#### Tradeoff

The Outbox Poller introduces a small delay between RabbitMQ recovery and job dispatch (one polling interval). This is acceptable for a file processing workload where near-real-time is not required. For lower latency, PostgreSQL `LISTEN/NOTIFY` can be used to wake the Poller immediately instead of polling on a fixed interval.
