# src/siphon/metrics.py
"""Prometheus metrics for Siphon.

Counters / histograms are module-level singletons.  Import this module wherever
instrumentation is needed; the registry is shared automatically via prometheus_client.
"""
from prometheus_client import Counter, Histogram, Gauge

jobs_total = Counter(
    "siphon_jobs_total",
    "Total number of extraction jobs processed",
    ["status"],  # success | failed | partial_success
)

job_duration_seconds = Histogram(
    "siphon_job_duration_seconds",
    "Extraction job duration in seconds",
    buckets=[1, 5, 15, 30, 60, 120, 300, 600, 1800],
)

rows_extracted_total = Counter(
    "siphon_rows_extracted_total",
    "Total rows successfully extracted and written",
)

queue_depth = Gauge(
    "siphon_queue_depth",
    "Current number of jobs in the queue (queued + running)",
)

schema_changes_total = Counter(
    "siphon_schema_changes_total",
    "Number of jobs where the Arrow schema differed from the stored hash",
)
