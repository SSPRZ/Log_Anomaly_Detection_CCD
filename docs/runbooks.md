# Runbooks

Pipeline alerts go to `#lap-pipeline` (Alertmanager receiver `slack-pipeline`), not to product teams.

## LapCanaryDeadMansSwitch (P1): no canary score for > 3 min
The synthetic canary logs every 30 s; if its windows stop being scored, the pipeline is not delivering.
1. `make logs` / `kubectl -n lap logs deploy/anomaly-scorer` — is the scorer running and consuming?
2. Consumer lag (Grafana "Consumer lag"): growing on `window-features`? → step 3. Growing on `anomaly-scorer`? → scale it
   (`kubectl scale`), or check the model load error in its log.
3. `window-features` stuck: check checkpoint errors; the watermark (`lap_watermark_seconds`) must advance.
4. Kafka: `kafka-topics.sh --describe` for under-replicated partitions; Strimzi operator events.
While the pipeline is down, Alertmanager inhibits application anomaly alerts (they would be meaningless).

## LapConsumerLagGrowing
* `parse-enrich` / `clickhouse-sink`: KEDA should already be scaling; verify `ScaledObject` status and max replicas
  (bounded by partition count — 24 on the raw path).
* `anomaly-scorer`: inspect `lap_e2e_latency_seconds`; a newly promoted model may be slow (ONNX backend falls back to
  sklearn if the parity check fails — see the scorer log).

## LapDLQRateHigh (> 0.1 % of ingested records)
1. Dashboard → Pipeline health → DLQ reasons. `missing:*` / `bad_ts` → a service broke the envelope; find it via the
   `service` field in the DLQ record and point the team to the onboarding checklist.
2. `late` → shipping delays > 35 s: check Fluent Bit backpressure (`storage.backlog`), node pressure.
3. After the fix, replay: reset the `parse-enrich` group to the DLQ incident's start time or re-ingest DLQ records.

## LapStoreInsertErrors
The sink does not commit on failure, so nothing is lost; detection is unaffected. Fix ClickHouse (disk, merges,
`system.errors`), and the sink resumes from its last committed offset.

## LapFeatureDrift (PSI > 0.2)
Open a model review: compare `data/drift_tickets.jsonl` features with recent deploys. Usually: retrain early
(`lap.py train`), or extend `deploy_rewarm_minutes` if a release legitimately changed behaviour.

## Bad model promoted (alert storm or silence)
```
python lap.py rollback          # previous champion; the scorer hot-reloads within one poll
```
Then inspect the failed version's metrics in MLflow and the shadow comparison (`anomalies.shadow`).

## Alert storm from one service after a deploy
Deploy events damp scores for 15 min. If a release changed "normal", raise the service's threshold offset in
`data/models/service_thresholds.json` (or let the weekly `lap.py tune` do it from feedback), and add a silence in
`alerting/rules.yaml` for known noisy templates.

## Consumer groups stall / "Histogram recorded value cannot be negative" in the broker log
Kafka < 4.3 group coordinator failing after the clock stepped backwards. Upgrade to 4.3+; as a stop-gap, restart the
broker. Symptom in the platform: every service looks "silent" and the canary dead-man's switch pages.

## Fluent Bit "Failed to produce ... Local: Fatal error"
The idempotent producer is in a fatal state (usually after the broker was fenced or its producer state was lost).
Fluent Bit does not recover by itself: Kubernetes restarts it through the `/api/v1/health` liveness probe; in
Compose run `docker compose restart fluent-bit`. Then check why the broker was fenced (`BrokerLifecycleManager ...
heartbeat ... timed out`): CPU or memory starvation of the broker.

## ClickHouse at high CPU with many small parts
`SELECT table, count() FROM system.parts WHERE active GROUP BY table`. Hundreds of parts per table means something
inserts row-by-row; the platform sinks batch (`flush_rows`, `flush_seconds`), so look for an ad-hoc writer.

