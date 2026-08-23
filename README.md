# Ontology-KnowledgeGraph-Data-Visualization
Turn your data into a queryable knowledge graph — no graph expertise needed. Powered by LLMs for ontology inference and natural language Q&A.

## Table of Contents

- [Data](#data)
  - [Supported Input Formats](#supported-input-formats)
  - [Upload Requirements](#upload-requirements)
  - [Data Validation](#data-validation)

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
