#!/usr/bin/env bash
# Create the platform topics (Section 4.2 table). Usage: create-topics.sh <bootstrap> <replication-factor>
# In Kubernetes the same topics are KafkaTopic CRs (ingestion/topics/kafka-topics.yaml) managed by Strimzi.
set -euo pipefail
BS="${1:-localhost:9092}"
RF="${2:-3}"
MIN_ISR=$(( RF > 1 ? 2 : 1 ))
KT=/opt/kafka/bin/kafka-topics.sh

create() {  # name partitions retention_ms
  $KT --bootstrap-server "$BS" --create --if-not-exists --topic "$1" --partitions "$2" \
      --replication-factor "$RF" \
      --config retention.ms="$3" --config min.insync.replicas="$MIN_ISR" \
      --config compression.type=zstd --config max.message.bytes=1048576
}

H=3600000
create logs.raw          24 $((72 * H))
create logs.parsed       24 $((72 * H))
create logs.features     12 $((7 * 24 * H))
create logs.sequences    12 $((72 * H))
create anomalies.scores   6 $((30 * 24 * H))
create alerts.outbound    3 $((30 * 24 * H))
create logs.dlq           6 $((7 * 24 * H))
create deploys.events     1 $((30 * 24 * H))
create alerts.feedback    1 $((365 * 24 * H))
$KT --bootstrap-server "$BS" --list
