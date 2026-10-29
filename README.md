# Distributed Log Anomaly Detection Platform

Implementation of *Distributed Log Anomaly Detection — Implementation Proposal v1.0* (Pranshu Thakkar, Oct 2026).
Services ship structured logs to Kafka; stream jobs parse them, redact PII, mine templates with Drain and build
one-minute behavioural features per service; a three-tier ML scorer compares every window with learned baselines;
and an alert router sends ranked, de-duplicated, explained alerts, with responder feedback flowing back into tuning
and training.

```
services ─▶ Fluent Bit ─▶ Kafka ─▶ parse-enrich ─▶ window-features ─▶ anomaly-scorer ─▶ alert-router ─▶ Slack / PagerDuty / e-mail
                                        │ PII, Drain       │ 1-min windows      │ T1 stats + T2 IForest   │ dedup, grouping,
                                        ▼                  ▼ + trace sessions   │ + T3 LSTM + rules        ▼ severity, feedback
                                    ClickHouse  ◀── sinks ──▶  MinIO/S3 Parquet  ◀── nightly training, MLflow, drift ──┘
```

Section-by-section traceability: [docs/architecture.md](docs/architecture.md).

## Results (M5 evaluation, lite stack)

7 days of simulated history (1.2 M log lines, benign deploys included) → nightly training → an evaluation period
with labelled incidents covering every fault type in proposal §9.1, compared with today's static rule and a more
sensitive static rule on the same incidents. Two independent runs (different traffic and incident schedules):

| Metric | Run A: 2 d, 12 incidents | Run B: 3 d, 18 incidents | Static ">50 err / 5 min" (B) | Static ">10 err / 5 min" (B) | Target |
|---|---|---|---|---|---|
| Incident precision | **0.88** | **0.89** | – (never fired) | 1.00 | ≥ 0.80 |
| Incident recall | **1.00** | **1.00** | 0.00 | 0.44 | ≥ 0.70 |
| F1 | **0.93** | **0.94** | 0.00 | 0.62 | |
| Alerts / service / day | 2.4 | 2.4 | 0 | 1.1 | < 5 |
| Median detection delay | 188 s | 155 s | – | 397 s | |
| e2e latency p95 (newest log in window → score) | 40.7 s | 40.9 s | – | – | < 60 s |

Reports: [eval-v5.md](evaluation/reports/eval-v5.md) (A), [eval-seed21.md](evaluation/reports/eval-seed21.md) (B).
Every alert carries its top drivers, the templates that moved most and redacted sample lines. In real-time mode
(`lap.py live`) the wall-clock e2e latency was 12–25 s (max 40 s).

Detected: downstream timeout, crash loop, silent service, bad deploy (new exception), memory leak (slow drift, via
the EWMA trend detector, ~19 min), and skipped workflow step (sequence model only).

## Live demo in one click (Windows)

Double-click **`demo.bat`**. It starts the platform in real time plus the dashboard (http://localhost:8502), trains
a model first if none exists, and opens the browser. After 2–3 minutes of normal traffic, use **Demo controls** in the
sidebar: pick a fault, click **Inject fault**, and the page refreshes itself until the explained alerts appear (about
2–3 minutes). Uses ports 8091/8502, so it runs alongside the Docker stack. Close its two windows to stop.

## Quick start (no Docker)

```bash
python -m venv .venv && .venv\Scripts\activate          # Windows (source .venv/bin/activate elsewhere)
pip install -r requirements-dev.txt
pip install torch --index-url https://download.pytorch.org/whl/cpu   # optional: LSTM tier (else Markov fallback)
```

```bash
python lap.py demo                              # history -> train -> fault suite -> report (~10 min)
```

```bash
python lap.py dashboard --db data/demo/lap.db   # timeline, explained alerts + feedback, logs, models, pipeline
```

```bash
python lap.py live                              # real time; fault injector on :8090/docs, metrics on :9108
```

```bash
curl -X POST localhost:8090/faults -H "content-type: application/json" -d "{\"kind\":\"silent\",\"service\":\"notifications\",\"duration_min\":5}"
```

Fault kinds: `downstream_timeout`, `crash_loop`, `silent`, `bad_deploy`, `memory_leak`, `skip_step`.

## Full stack (Docker Compose)

```bash
make up        # Kafka 4.3 (KRaft), Schema Registry, Fluent Bit, ClickHouse, SeaweedFS (S3), MLflow+Postgres,
               # Prometheus, Alertmanager, Grafana, the simulated shop and one container per stage
docker compose -f deploy/compose/docker-compose.yml --profile jobs run --rm trainer bootstrap --days 1
make train     # train + promote (needs >= ~4 h of windows; bootstrap seeds a day of history)
make inject KIND=downstream_timeout SERVICE=payments MIN=10
```

Give Docker Desktop at least 6–8 GB of memory: all 20 containers fit in 3.7 GB, but the VM then runs at ~95 %
memory and the single Kafka broker needs the relaxed KRaft timeouts set in the compose file to stay unfenced.

Grafana http://localhost:3000 · dashboard http://localhost:8501 · MLflow http://localhost:5000 ·
fault injector http://localhost:8090/docs · Prometheus http://localhost:9090.
Optional profiles: `--profile flink` (Flink 2 cluster for `stream/flink`), `--profile connect` (Kafka Connect sinks).
From a Windows host, set `storage.clickhouse.compress: false` for tools that talk to `localhost:8123` (Docker
Desktop's port forwarder resets compressed HTTP); containers use compression normally.

## Production

* `deploy/terraform` — VPC across 3 AZs, EKS with kafka/compute/storage node pools, KMS-encrypted S3 buckets with
  lifecycle rules, IRSA role.
* `deploy/helm/lap` — umbrella chart: Strimzi Kafka (KRaft, mTLS, per-principal ACLs), Flink deployments,
  stage Deployments with KEDA lag-based autoscaling, CronJobs (train, tune, drift, shadow promotion),
  default-deny NetworkPolicies (only alert-router has egress), External Secrets.
* `deploy/argocd` — app-of-apps (operators first), Image Updater for staging, PR-gated production.
* `.github/workflows` — lint/type/unit (coverage gate) → schema compatibility, helm lint, terraform validate →
  distroless build, Trivy, SBOM, cosign → Testcontainers integration → staging → prod PR; nightly model pipeline.

## Repository layout

| Path | Contents |
|---|---|
| `services/` | shared logging library (Python, Go); simulated e-commerce services + fault model |
| `ingestion/` | Fluent Bit configs, Kafka topic CRs/script, Avro schemas + compatibility check |
| `stream/` | parse-enrich (redaction, Drain), window-features, sessions; `flink/` PyFlink + Flink SQL |
| `detection/` | T1 baseline, T2 isolation forest (ONNX), T3 sequence model, rules, ensemble, explanations, scorer, training, registry, drift |
| `alerting/` | router, routing rules, notifiers, Slack/HTTP feedback API, threshold tuning |
| `storage/` | ClickHouse DDL, SQLite/ClickHouse stores, Kafka Connect configs |
| `pipeline/` | stage runtime (Kafka or in-process), Parquet archive, lite orchestrator |
| `dashboard/` | Streamlit UI |
| `chaos/` | fault-injector API, Chaos Mesh experiments, Locust load profile |
| `evaluation/` | incident-level metrics, end-to-end evaluation, target check, data validation, HDFS/BGL harness |
| `deploy/` | compose, helm, argocd, terraform |
| `docs/` | architecture/traceability, runbooks, onboarding, ADRs |

## Operations

```bash
python lap.py train [--promote]        # train + evaluation gate (+ shadow mode unless --promote)
python lap.py promote --after-shadow   # promote challenger once it has shadowed for 3 days
python lap.py rollback                 # previous champion; scorers hot-reload
python lap.py tune                     # weekly feedback-driven per-service thresholds
python lap.py drift                    # PSI report; > 0.2 opens a ticket
```

Runbooks: [docs/runbooks.md](docs/runbooks.md). Onboarding a service: [docs/onboarding.md](docs/onboarding.md).

## Tests

```bash
python -m pytest            # 68 unit + 2 end-to-end (simulate -> train -> faults -> explained alerts)
ruff check .
```

`tests/integration` runs against real Kafka and ClickHouse: via Testcontainers in CI, or against a running stack with
`LAP_IT_KAFKA=localhost:9094 LAP_IT_CLICKHOUSE=localhost:8123:default:lap python -m pytest tests/integration`.

## What was verified here and what was not

* **Lite stack** (single process): every stage, training/registry, router, dashboard, real-time mode, fault
  injector; 68 unit + 2 end-to-end tests; the evaluation runs above.
* **Docker Compose full stack** (all 20 services on Docker Desktop): logs flow shop → Fluent Bit → Kafka 4.3 →
  parse-enrich → window-features → scorer → router; ClickHouse holds logs/templates/features/anomalies/alerts/
  deploys; Parquet lands in SeaweedFS by `dt/hour`; training reads ClickHouse, registers in MLflow (Postgres + S3)
  and the running scorer hot-reloads the champion; injected faults (bank-gateway timeout, bad deploy) raised
  explained alerts in ClickHouse and Alertmanager (e.g. `KeyError: 'currency'` template, 5xx/error-ratio drivers,
  158 s after a bad deploy); alerts auto-resolve; Prometheus scrapes all stages (1 h p95 latency SLI 29 s); Grafana's
  dashboard and ClickHouse datasource are provisioned; integration tests pass against the live Kafka and ClickHouse.
* **Not exercised** (no cluster; Terraform provider downloads blocked by the network): Flink jobs, Helm/Argo CD/
  Terraform deployments, the GitHub Actions workflows. `terraform fmt` passes.

The smoke test found and fixed real defects: per-poll ClickHouse inserts (part explosion), offsets committed
before S3 writes, a `;` inside a DDL comment, MinIO images withdrawn upstream (replaced by SeaweedFS), Kafka 4.0/4.1
group-coordinator failure when the VM clock steps back (fixed upstream in 4.3), a JVM-launching Kafka healthcheck
that got the broker fenced, router crashes and duplicate alerts on restart, Alertmanager alerts expiring without
re-send, a dead-man rule firing per stage, the drift detector's 20-minute tail after acute incidents, a YAML
folding bug that ran MLflow without its Postgres/S3 options, and CLI pass-through for `simulate`/`evaluate`.
