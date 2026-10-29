"""Pipeline self-observability (Section 6): Prometheus metrics shared by all stages."""
from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, start_http_server

REGISTRY = CollectorRegistry()

RECORDS_IN = Counter("lap_records_in_total", "Records consumed", ["stage"], registry=REGISTRY)
RECORDS_OUT = Counter("lap_records_out_total", "Records produced", ["stage", "topic"], registry=REGISTRY)
DLQ = Counter("lap_dlq_total", "Records sent to the DLQ", ["stage", "reason"], registry=REGISTRY)
LATE = Counter("lap_late_records_total", "Records arriving after the watermark", ["kind"], registry=REGISTRY)
E2E_LATENCY = Histogram(
    "lap_e2e_latency_seconds", "Newest log event time in a window -> anomaly score emitted",
    buckets=(1, 2, 5, 10, 20, 30, 45, 60, 90, 120, 300), registry=REGISTRY)
SCORE = Histogram("lap_anomaly_score", "Ensemble score distribution", ["service"],
                  buckets=(0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0), registry=REGISTRY)
ALERTS = Counter("lap_alerts_total", "Alerts emitted", ["service", "severity"], registry=REGISTRY)
CONSUMER_LAG = Gauge("lap_consumer_lag", "Uncommitted records per consumer group", ["group"], registry=REGISTRY)
WATERMARK = Gauge("lap_watermark_seconds", "Current event-time watermark", registry=REGISTRY)
CANARY_LAST = Gauge("lap_canary_last_score_ts", "Wall time the canary was last scored", registry=REGISTRY)
MODEL_VERSION = Gauge("lap_model_version", "Champion model version in use", ["group"], registry=REGISTRY)
STORE_ERRORS = Counter("lap_store_insert_errors_total", "Storage insert failures", ["table"], registry=REGISTRY)
PSI = Gauge("lap_feature_psi", "Population stability index vs training data", ["feature"], registry=REGISTRY)


def serve(port: int) -> None:
    start_http_server(port, registry=REGISTRY)
