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

**Tradeoff:** RabbitMQ does not retain messages after consumption. If audit log or message replay is needed in the future, Kafka would be the better fit.

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
| Message replay / audit | Not needed | Migrate from RabbitMQ to Kafka for event sourcing and replay capability |
| Job persistence | In-flight jobs lost on Worker restart (re-queued via RabbitMQ) | Outbox pattern ensures no job is lost even if both API and queue are temporarily unavailable |
| Backpressure | Queue grows unbounded | Cap queue size, return HTTP 429 when limit reached |
