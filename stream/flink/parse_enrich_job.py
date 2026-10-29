"""PyFlink deployment of the parse-enrich job (cluster path, Flink 2.x).

Reuses the exact Python logic of stream/parse_enrich.py inside a
KeyedProcessFunction keyed by service, so the Drain template dictionary is
Flink keyed state (checkpointed every 30 s to object storage) and is also
snapshotted to ClickHouse ``log_templates`` by the sink.

Submit (Flink Kubernetes Operator FlinkDeployment, or locally):
    flink run -py stream/flink/parse_enrich_job.py -pyfs /opt/lap \
        --bootstrap kafka:9092 --parallelism 24

Exactly-once: Kafka source + transactional Kafka sink (DeliveryGuarantee.EXACTLY_ONCE)
with checkpointing enabled. Downstream consumers use isolation.level=read_committed.
"""
from __future__ import annotations

import argparse
import json
import pickle

from pyflink.common import Duration, Types, WatermarkStrategy
from pyflink.common.serialization import SimpleStringSchema
from pyflink.datastream import CheckpointingMode, KeyedProcessFunction, OutputTag, RuntimeContext, \
    StreamExecutionEnvironment
from pyflink.datastream.connectors.base import DeliveryGuarantee
from pyflink.datastream.connectors.kafka import (KafkaOffsetsInitializer, KafkaRecordSerializationSchema, KafkaSink,
                                                 KafkaSource)
from pyflink.datastream.state import ValueStateDescriptor

DLQ = OutputTag("dlq", Types.STRING())


class ParseEnrichFn(KeyedProcessFunction):
    def open(self, ctx: RuntimeContext):
        from common.config import load_config
        from stream.parse_enrich import ParseEnrich

        self.job = ParseEnrich(load_config())
        self.state = ctx.get_state(ValueStateDescriptor("drain", Types.PICKLED_BYTE_ARRAY()))

    def process_element(self, value, ctx):
        svc = ctx.get_current_key()
        snap = self.state.value()
        if snap is not None and svc not in self.job.drains:
            self.job.drains[svc] = pickle.loads(snap)
        try:
            raw = json.loads(value)
        except ValueError:
            raw = {"_raw": value}
        parsed, dlq = self.job.process(raw)
        if parsed is not None:
            # persist the tree when it changes shape, and periodically as templates generalise
            self.n = getattr(self, "n", 0) + 1
            if parsed["template_created"] or self.n % 1000 == 0:
                self.state.update(pickle.dumps(self.job.drain_for(svc)))
            yield json.dumps(parsed, separators=(",", ":"))
        else:
            yield DLQ, json.dumps(dlq, separators=(",", ":"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bootstrap", default="kafka:9092")
    ap.add_argument("--parallelism", type=int, default=24)
    a, _ = ap.parse_known_args()

    env = StreamExecutionEnvironment.get_execution_environment()
    env.set_parallelism(a.parallelism)
    env.enable_checkpointing(30_000, CheckpointingMode.EXACTLY_ONCE)

    source = (KafkaSource.builder().set_bootstrap_servers(a.bootstrap).set_topics("logs.raw")
              .set_group_id("flink-parse-enrich").set_starting_offsets(KafkaOffsetsInitializer.committed_offsets())
              .set_value_only_deserializer(SimpleStringSchema()).build())

    def sink(topic):
        return (KafkaSink.builder().set_bootstrap_servers(a.bootstrap)
                .set_record_serializer(KafkaRecordSerializationSchema.builder().set_topic(topic)
                                       .set_value_serialization_schema(SimpleStringSchema()).build())
                .set_delivery_guarantee(DeliveryGuarantee.EXACTLY_ONCE)
                .set_transactional_id_prefix(f"lap-{topic}").build())

    stream = env.from_source(source, WatermarkStrategy.for_bounded_out_of_orderness(Duration.of_seconds(5))
                             .with_idleness(Duration.of_seconds(60)), "logs.raw")
    parsed = (stream.key_by(lambda v: (json.loads(v).get("service") if v.startswith("{") else None) or "unknown",
                            key_type=Types.STRING())
              .process(ParseEnrichFn(), output_type=Types.STRING()).name("parse-enrich"))
    parsed.sink_to(sink("logs.parsed")).name("logs.parsed")
    parsed.get_side_output(DLQ).sink_to(sink("logs.dlq")).name("logs.dlq")
    env.execute("lap-parse-enrich")


if __name__ == "__main__":
    main()
