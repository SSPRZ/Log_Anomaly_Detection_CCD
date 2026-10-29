-- ClickHouse schema (Section 4.4). Applied in order by storage/store.py:ClickHouseStore.migrate()
-- and by the clickhouse container's docker-entrypoint-initdb.d in the compose stack.
-- Event times are Float64 epoch seconds to match the pipeline; materialised DateTime64
-- columns drive partitioning and TTLs.

CREATE TABLE IF NOT EXISTS logs
(
    ts           Float64,
    event_time   DateTime64(3, 'UTC') MATERIALIZED toDateTime64(ts, 3, 'UTC'),
    service      LowCardinality(String),
    env          LowCardinality(String),
    level        LowCardinality(String),
    host         LowCardinality(String),
    trace_id     String,
    template_id  LowCardinality(String),
    msg          String CODEC(ZSTD(3)),
    attrs        String CODEC(ZSTD(3)),
    INDEX msg_tokens msg TYPE tokenbf_v1(32768, 3, 0) GRANULARITY 4,
    INDEX trace_idx trace_id TYPE bloom_filter(0.01) GRANULARITY 4
)
ENGINE = MergeTree
PARTITION BY toDate(event_time)
ORDER BY (service, level, event_time)
TTL toDateTime(event_time) + INTERVAL 14 DAY DELETE;

CREATE TABLE IF NOT EXISTS log_templates
(
    template_id String,
    namespace   LowCardinality(String),
    pattern     String,
    size        UInt64,
    first_seen  Float64,
    last_seen   Float64
)
ENGINE = ReplacingMergeTree(last_seen)
ORDER BY template_id;

CREATE TABLE IF NOT EXISTS features_1m
(
    service            LowCardinality(String),
    window_start       Float64,
    window_end         Float64,
    revision           UInt16,
    total_count        Nullable(Float64),
    count_delta_week   Nullable(Float64),
    distinct_hosts     Nullable(Float64),
    error_count        Nullable(Float64),
    warn_count         Nullable(Float64),
    error_ratio        Nullable(Float64),
    fatal              Nullable(Float64),
    unique_templates   Nullable(Float64),
    new_templates      Nullable(Float64),
    template_entropy   Nullable(Float64),
    p50_latency_ms     Nullable(Float64),
    p95_latency_ms     Nullable(Float64),
    p99_latency_ms     Nullable(Float64),
    ratio_5xx          Nullable(Float64),
    seconds_since_last Nullable(Float64),
    payload            String CODEC(ZSTD(3)),
    window_time        DateTime MATERIALIZED toDateTime(toUInt32(window_start), 'UTC')
)
ENGINE = ReplacingMergeTree(revision)
PARTITION BY toYYYYMM(window_time)
ORDER BY (service, window_start)
TTL window_time + INTERVAL 90 DAY DELETE;

CREATE TABLE IF NOT EXISTS anomalies
(
    service       LowCardinality(String),
    window_start  Float64,
    window_end    Float64,
    score         Float64,
    is_anomalous  UInt8,
    stats         Nullable(Float64),
    iforest       Nullable(Float64),
    sequence      Nullable(Float64),
    rule          String,
    top_drivers   String,
    explanation   String CODEC(ZSTD(3)),
    model_version String,
    shadow        String,
    emitted_at    Float64,
    e2e_latency_s Nullable(Float64),
    window_time   DateTime MATERIALIZED toDateTime(toUInt32(window_start), 'UTC')
)
ENGINE = ReplacingMergeTree(emitted_at)
PARTITION BY toYYYYMM(window_time)
ORDER BY (service, window_start)
TTL window_time + INTERVAL 365 DAY DELETE;

CREATE TABLE IF NOT EXISTS alerts
(
    alert_id          String,
    incident_id       String,
    service           LowCardinality(String),
    severity          LowCardinality(String),
    status            LowCardinality(String),
    opened_at         Float64,
    updated_at        Float64,
    first_window      Float64,
    last_window       Float64,
    score             Float64,
    dominant_template String,
    payload           String,
    channels          String
)
ENGINE = ReplacingMergeTree(updated_at)
ORDER BY alert_id;

CREATE TABLE IF NOT EXISTS alert_feedback
(
    alert_id String,
    verdict  LowCardinality(String),
    user     String,
    comment  String,
    ts       Float64
)
ENGINE = MergeTree
ORDER BY (alert_id, ts);

CREATE TABLE IF NOT EXISTS dlq
(
    ts     Float64,
    stage  LowCardinality(String),
    reason LowCardinality(String),
    record String CODEC(ZSTD(3))
)
ENGINE = MergeTree
ORDER BY ts
TTL toDateTime(toUInt32(ts)) + INTERVAL 7 DAY DELETE;

CREATE TABLE IF NOT EXISTS deploys
(
    ts         Float64,
    service    LowCardinality(String),
    version    String,
    commit_sha String
)
ENGINE = MergeTree
ORDER BY (service, ts)
