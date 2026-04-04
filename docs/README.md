# Siphon Documentation

Siphon is a self-hosted data pipeline service that extracts data from SQL databases and SFTP sources and writes it as Parquet files to S3-compatible object storage. It replaces Apache Spark for Bronze-layer extraction workloads.

---

## Reading order for new contributors

1. **[architecture.md](architecture.md)** — How the system works: components, data flow, design decisions, glossary. Start here.
2. **[deployment.md](deployment.md)** — Running with Docker Compose or Kubernetes; all environment variables.
3. **[auth.md](auth.md)** — JWT, refresh tokens, roles, user management, secrets.
4. **[connections.md](connections.md)** — Storing credentials; SQL, SFTP, and S3 connection types; security.
5. **[pipelines.md](pipelines.md)** — Creating pipelines; full refresh vs incremental; DQ guards; scheduling.
6. **[api-reference.md](api-reference.md)** — Every HTTP endpoint with request/response examples.
7. **[frontend.md](frontend.md)** — React SPA structure; auth flow; components; dev server.
8. **[contributing.md](contributing.md)** — Local setup; running tests; adding source/destination plugins.

---

## Quick links

| I want to… | Go to |
|---|---|
| Understand how a pipeline run works end-to-end | [architecture.md → Request lifecycle](architecture.md#request-lifecycle) |
| Add a new database source type | [contributing.md → Adding a new source plugin](contributing.md#adding-a-new-source-plugin) |
| Set up incremental extraction | [pipelines.md → Incremental](pipelines.md#incremental) |
| Configure cron schedules | [pipelines.md → Scheduling internals](pipelines.md#scheduling-internals) |
| Set up SSRF protection | [connections.md → SSRF protection](connections.md#ssrf-protection) |
| Deploy to Kubernetes | [deployment.md → Kubernetes](deployment.md#kubernetes) |
| Rotate the encryption key | [connections.md → Encryption at rest](connections.md#encryption-at-rest) |
| Add a new UI page | [frontend.md → Adding a new page](frontend.md#adding-a-new-page) |
| Understand JWT + refresh tokens | [auth.md → How authentication works](auth.md#how-authentication-works) |
| Integrate with Airflow | [pipelines.md → Legacy API](pipelines.md#legacy-api-airflow-integration) |
| See what's planned next | [roadmap.md](roadmap.md) |
