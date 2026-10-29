-- window-features job in Flink SQL (Appendix B, extended to the full Section 4.3 feature table).
-- Submitted with the SQL client / a FlinkSessionJob. Entropy, new-template counts and
-- percentiles are user-defined functions shipped in stream/flink/udfs.py
-- (Python UDF/UDAF registered with CREATE TEMPORARY SYSTEM FUNCTION ... LANGUAGE PYTHON).
-- The liveness feature (seconds_since_last for silent services) needs timers and is produced by
-- the Python job (pipeline/stages.py FeatureStage) or a small KeyedProcessFunction; this SQL
-- covers the per-window aggregates.

SET 'execution.checkpointing.interval' = '30s';
SET 'execution.checkpointing.mode' = 'EXACTLY_ONCE';
SET 'table.exec.source.idle-timeout' = '60 s';

CREATE TEMPORARY SYSTEM FUNCTION PERCENTILE_APPROX AS 'udfs.percentile_approx' LANGUAGE PYTHON;
CREATE TEMPORARY SYSTEM FUNCTION TEMPLATE_ENTROPY  AS 'udfs.template_entropy'  LANGUAGE PYTHON;
CREATE TEMPORARY SYSTEM FUNCTION TEMPLATE_HIST     AS 'udfs.template_hist'     LANGUAGE PYTHON;

CREATE TABLE logs_parsed (
  `ts`               DOUBLE,
  `service`          STRING,
  `level`            STRING,
  `host`             STRING,
  `template_id`      STRING,
  `template_created` BOOLEAN,
  `attrs`            MAP<STRING, STRING>,
  event_time AS TO_TIMESTAMP_LTZ(CAST(`ts` * 1000 AS BIGINT), 3),
  WATERMARK FOR event_time AS event_time - INTERVAL '5' SECOND
) WITH (
  'connector' = 'kafka',
  'topic' = 'logs.parsed',
  'properties.bootstrap.servers' = 'kafka:9092',
  'properties.group.id' = 'flink-window-features',
  'properties.isolation.level' = 'read_committed',
  'scan.startup.mode' = 'group-offsets',
  'properties.auto.offset.reset' = 'earliest',
  'format' = 'json',
  'json.ignore-parse-errors' = 'true'
);

CREATE TABLE logs_features (
  service          STRING,
  window_start     DOUBLE,
  window_end       DOUBLE,
  total_count      BIGINT,
  distinct_hosts   BIGINT,
  error_count      BIGINT,
  warn_count       BIGINT,
  error_ratio      DOUBLE,
  fatal            INT,
  unique_templates BIGINT,
  new_templates    BIGINT,
  template_entropy DOUBLE,
  template_hist    ARRAY<BIGINT>,
  p50_latency_ms   DOUBLE,
  p95_latency_ms   DOUBLE,
  p99_latency_ms   DOUBLE,
  ratio_5xx        DOUBLE,
  max_event_ts     DOUBLE
) WITH (
  'connector' = 'kafka',
  'topic' = 'logs.features',
  'properties.bootstrap.servers' = 'kafka:9092',
  'sink.delivery-guarantee' = 'exactly-once',
  'sink.transactional-id-prefix' = 'lap-features',
  'key.format' = 'raw',
  'key.fields' = 'service',
  'value.format' = 'json'
);

INSERT INTO logs_features
SELECT
  service,
  CAST(UNIX_TIMESTAMP(CAST(window_start AS STRING)) AS DOUBLE)                          AS window_start,
  CAST(UNIX_TIMESTAMP(CAST(window_end AS STRING)) AS DOUBLE)                            AS window_end,
  COUNT(*)                                                                              AS total_count,
  COUNT(DISTINCT host)                                                                  AS distinct_hosts,
  SUM(CASE WHEN level IN ('ERROR', 'FATAL') THEN 1 ELSE 0 END)                          AS error_count,
  SUM(CASE WHEN level = 'WARN' THEN 1 ELSE 0 END)                                       AS warn_count,
  SUM(CASE WHEN level IN ('ERROR', 'FATAL') THEN 1 ELSE 0 END) * 1.0 / COUNT(*)         AS error_ratio,
  MAX(CASE WHEN level = 'FATAL' THEN 1 ELSE 0 END)                                      AS fatal,
  COUNT(DISTINCT template_id)                                                           AS unique_templates,
  SUM(CASE WHEN template_created THEN 1 ELSE 0 END)                                     AS new_templates,
  TEMPLATE_ENTROPY(template_id)                                                         AS template_entropy,
  TEMPLATE_HIST(template_id)                                                            AS template_hist,
  PERCENTILE_APPROX(CAST(attrs['latency_ms'] AS DOUBLE), 0.50)                          AS p50_latency_ms,
  PERCENTILE_APPROX(CAST(attrs['latency_ms'] AS DOUBLE), 0.95)                          AS p95_latency_ms,
  PERCENTILE_APPROX(CAST(attrs['latency_ms'] AS DOUBLE), 0.99)                          AS p99_latency_ms,
  SUM(CASE WHEN CAST(attrs['status'] AS INT) >= 500 THEN 1 ELSE 0 END) * 1.0
      / GREATEST(SUM(CASE WHEN attrs['status'] IS NOT NULL THEN 1 ELSE 0 END), 1)       AS ratio_5xx,
  MAX(`ts`)                                                                             AS max_event_ts
FROM TABLE(TUMBLE(TABLE logs_parsed, DESCRIPTOR(event_time), INTERVAL '1' MINUTE))
GROUP BY service, window_start, window_end;
