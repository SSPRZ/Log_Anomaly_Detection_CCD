# Architecture and proposal traceability

This maps every section of *Distributed Log Anomaly Detection — Implementation Proposal v1.0* to the code
that implements it, and notes where the implementation deliberately differs.

## Data path

```
services (loglib) ─stdout─▶ Fluent Bit ─▶ logs.raw ─▶ parse-enrich ─▶ logs.parsed ─▶ window-features ─▶ logs.features ─┐
                                              │             │ (DLQ)                      │ (sessions)                    │
                                              │             ▼                            ▼                               ▼
                                              │         logs.dlq                    logs.sequences ─────────▶ anomaly-scorer ─▶ anomalies.scores ─▶ alert-router ─▶ alerts.outbound
                                              │                                                       deploys.events ─┘                                  │
                                              ▼                                                                                                          ▼
                                       s3-sink (Parquet)        clickhouse-sink: logs, log_templates, features_1m, anomalies, alerts, alert_feedback   Slack / PagerDuty / e-mail
```

Every hop is a topic; every stage is a consumer group that commits only after its output is flushed
(at-least-once; the router de-duplicates). Replays = reset a group's offsets or read the Parquet archive.

## Section → implementation

| Proposal | Implementation |
|---|---|
| 4.1 Shared logging library, envelope | `services/loglib/python/lalog.py`, `services/loglib/go/lalog.go`; envelope validation `common/envelope.py`; Avro `ingestion/schemas/log_envelope.avsc` |
| 4.1 Fluent Bit DaemonSet / shipping | `ingestion/fluent-bit/fluent-bit-k8s.yaml` (K8s, mTLS, zstd, filesystem buffering), `fluent-bit-compose.conf` |
| 4.2 Kafka topics, RF=3, acks=all, idempotence | `ingestion/topics/kafka-topics.yaml` (Strimzi CRs), `create-topics.sh`; producer settings `common/bus.py:KafkaBus` |
| 4.2 Schema Registry, BACKWARD compatibility in CI | `ingestion/schemas/*.avsc`, `ingestion/schemas/check_compat.py`, CI job `contract` |
| 4.3 parse-enrich: parse JSON/legacy, UTC, PII redaction, Drain templates | `stream/parse_enrich.py`, `stream/redaction.py`, `stream/drain.py`; Flink: `stream/flink/parse_enrich_job.py` |
| 4.3 window-features: 1-min tumbling event-time windows, watermarks, 30 s lateness, feature table | `stream/window_features.py` (per-source watermarks with idleness, revisions for late data, silence windows); Flink SQL: `stream/flink/window_features.sql` + `udfs.py` |
| 4.3 template dictionary stable across restarts | template IDs derived from service + template; snapshot/restore through `log_templates` (`pipeline/stages.py:ParseStage`) |
| 4.4 ClickHouse tables, TTLs, token Bloom filter | `storage/clickhouse/ddl/001_init.sql`, `storage/store.py:ClickHouseStore` |
| 4.4 MinIO/S3 Parquet archive | `pipeline/archive.py`, stage `s3-sink`; Kafka Connect alternative `storage/connect/s3-sink.json` |
| 4.5 T1 robust z-score vs hour-of-week baseline + EWMA trend | `detection/baseline.py` |
| 4.5 T2 Isolation Forest per service group, nightly, ONNX | `detection/iforest.py` (ONNX via skl2onnx + onnxruntime with parity check) |
| 4.5 T3 DeepLog-style LSTM on per-trace template sequences | `detection/sequence.py` (PyTorch LSTM; Markov fallback), sessions from `Sessionizer` |
| 4.5 Hard rules (new ERROR template ≥ N, liveness) | `detection/rules.py` |
| 4.5 Ensemble + 2-of-3 debounce, rules bypass | `detection/ensemble.py`, `detection/scorer.py` |
| 4.5 Explanations (top-3 features, moved templates, 5 samples) | `detection/explain.py` |
| 4.5 Serving: stateless scorer, champion hot-reload | `detection/scorer.py:Scorer.reload` (polls registry mtime) |
| 4.5 Lifecycle: nightly train, holdout + injected anomalies, F1 gate, alert budget, shadow 3 days | `detection/train.py`, `detection/registry.py` (champion/challenger/rollback, MLflow mirror) |
| 4.5 Drift (PSI) and deploy re-warm | `detection/drift.py`; `deploys.events` → damped scoring for 15 min (`Scorer.on_deploy`) |
| 4.6 De-dup 15 min, grouping of calling services within 2 min | `alerting/router.py` (topology in `config/topology.yaml`) |
| 4.6 Severity P1/P2/P3, routing rules in Git | `alerting/router.py:severity`, `alerting/rules.yaml` |
| 4.6 Alertmanager → Slack / e-mail / PagerDuty | `alerting/notifiers.py`, `deploy/compose/alertmanager/alertmanager.yml` |
| 4.6 Feedback buttons, weekly tuning, labels for re-ranker | Slack Block Kit buttons (`SlackNotifier.payload`), `alerting/feedback.py` (signed Slack actions + HTTP), dashboard buttons, `alerting/tuning.py`, `train.feedback_labels` |
| 5.1 Local `make up`, staging, production | `deploy/compose/docker-compose.yml`, `Makefile`; Helm values per env |
| 5.2 Terraform, Helm umbrella, Argo CD app-of-apps, operators, External Secrets, MLflow | `deploy/terraform`, `deploy/helm/lap`, `deploy/argocd` |
| 5.3 CI/CD stages incl. Trivy, SBOM, cosign, Testcontainers, promotion PR | `.github/workflows/ci.yml`, `.github/workflows/model.yml`, `tests/integration` |
| 5.4 Capacity plan | `deploy/terraform/variables.tf` node pools, Helm resources/KEDA limits |
| 6 Pipeline SLIs/SLOs, canary dead-man's switch | `common/metrics.py`, `deploy/compose/prometheus/rules.yml`, canary records in the simulator + `AlertRouter.tick` |
| 7 Security: mTLS, ACLs, RBAC, NetworkPolicies, encryption, supply chain | `deploy/helm/lap/templates/kafka.yaml` (KafkaUser ACLs), `security.yaml`, Terraform KMS/S3 settings, CI scans |
| 8 Failure handling | at-least-once commits, DLQ, checkpointed window state (`FeatureStage.checkpoint`), T1 fallback, rollback |
| 9.1 Demo workload, fault injector, Chaos Mesh | `services/simulator/*`, `chaos/fault_injector.py`, `chaos/chaos-mesh/experiments.yaml`, `chaos/locust/locustfile.py` |
| 9.2 Metrics: incident P/R/F1, delay, hygiene, latency; HDFS/BGL | `evaluation/metrics.py`, `evaluation/run_eval.py`, `evaluation/benchmarks/loghub.py` |
| 13 "evaluation report at M5 comparing ... against the current threshold rules" | `evaluation/reports/*.md` (static ">50 errors / 5 min" baseline on the same incidents) |

## Deliberate deviations

* **Two runtimes for the stream jobs.** The Python `ParseEnrich`/`WindowFeatures` classes are the reference
  implementation; they run in the lite stack and as Kafka consumer containers. The Flink jobs reuse the same
  Python parsing code (PyFlink) and express window aggregates in Flink SQL. Silence detection
  (`seconds_since_last` for services that send nothing) needs timers and is produced by the Python job.
* **Per-source watermarks.** The Python job tracks a watermark per service and takes the minimum over active
  sources (idleness 15 s), the same semantics as Flink's per-partition watermarks with `withIdleness`.
* **T2 scoring.** An isolation forest cannot score points *beyond* its training range higher than the range's
  edge, so the T2 score is `max(calibrated forest score, out-of-range excess)`. The 64 hashed template buckets
  are summarised as one `mix_shift` feature (Jensen–Shannon divergence from the service's normal mix) because
  64 noisy proportions drown the informative features in random splits.
* **Sequence attribution.** Only the first deviation of a trace counts, and it is blamed on the service whose
  step was *expected* — after the first deviation every later step looks wrong as well.
* **Hard rule over an open statistical alert** opens a new alert in the same incident instead of being
  merged, so a high-precision signal is never buried under an earlier alert.
* **Kafka Connect** connector configs are provided (`storage/connect`), but the default stack uses the equivalent
  `clickhouse-sink`/`s3-sink` stages, which have no JVM dependency.
* **Object storage in the local stack is SeaweedFS** (S3 API on :8333). MinIO's community images were withdrawn
  from Docker Hub and quay.io; production uses the cloud bucket through the same `s3://` URIs.
* **Kafka 4.3.0 or newer.** In 4.0/4.1 the group coordinator records event-queue time in an HdrHistogram without
  clamping negatives; when the host clock steps back (common after laptop sleep in Docker Desktop/WSL) offset
  commits fail and consumer groups stall. 4.3.0's `HdrHistogram.record` clamps with `Math.max(value, 0)`.
* **Sinks batch.** `clickhouse-sink` and `s3-sink` buffer across polls and commit offsets only after a successful
  write (one insert per poll would create a MergeTree part per poll).

