---
name: project_siphon
description: Core context for the Siphon project — what it is, key decisions, and architectural choices
type: project
---

Siphon is a lightweight data extraction service (FastAPI + Python) that replaces Apache Spark in the Bronze layer of a medallion data pipeline. Orchestrated by Apache Airflow, which calls Siphon via HTTP instead of launching SparkKubernetesOperator jobs.

**Why:** Spark adds 30–60s startup, 2GB+ RAM, and a 2GB image for jobs that are just `SELECT ... FROM table` + write Parquet to MinIO. 200+ Bronze DAGs follow this pattern.

**Status:** Spec is complete and has been reviewed from 4 angles:
1. General engineering (consistency, contradictions)
2. Senior data engineer (type fidelity, timezone, idempotency, Oracle)
3. Senior DevOps/infra (Dockerfile, Kubernetes, graceful shutdown, health probes)
4. Security (SSRF, API key auth, SFTP host key, path validation, credential isolation)

The full spec lives at `/home/lucasnvk/projects/siphon/claude.md`.

**Key decisions:**
- Name: `siphon`
- SQL engine: ConnectorX (Rust, Arrow-native). Oracle exception: pandas + oracledb thin mode
- Parquet write: PyArrow built-in S3FileSystem (NOT s3fs)
- SFTP: Paramiko, one SSH connection per job, RejectPolicy for host key
- Queue: asyncio in-memory, ThreadPoolExecutor for blocking I/O
- Airflow: POST /jobs + polling with log_offset (NOT sync POST /extract)
- Scalability v1: single instance + SIPHON_MAX_WORKERS. v2: StatefulSet headless service
- `existing_data_behavior="delete_matching"` for idempotent daily runs
- `return_type="arrow"` in ConnectorX (arrow2 deprecated)
- Oracle uses oracledb thin mode (no instantclient → stays under 500MB)
- Graceful drain on SIGTERM before shutdown
- Security: SIPHON_API_KEY, SIPHON_ALLOWED_HOSTS (SSRF), SIPHON_ALLOWED_S3_PREFIX, RejectPolicy SFTP

**How to apply:** All implementation must follow claude.md. When in doubt about a decision, the spec is authoritative.
