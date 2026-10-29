# ADR 0001 — Stack choices

* **Status:** accepted (mirrors proposal Section 12)
* **Context:** need event-time streaming, cheap analytical storage, label-free detection, GitOps operations.

| Decision | Chosen | Rejected | Reason |
|---|---|---|---|
| Stream processor | Flink (+ Python reference jobs) | Kafka Streams, Spark SS, Bytewax | event-time windows, large keyed state, exactly-once |
| Store | ClickHouse | OpenSearch, Loki | storage cost, fast aggregations for features/evaluation |
| Models | Stats + IForest, then LSTM | autoencoders, supervised, LLM | no labels; fast; explainable |
| Shipping | Fluent Bit | Filebeat, Vector | footprint, native Kafka output |
| Deployment | Kubernetes + Argo CD | VMs/Ansible, Compose only | operators automate day-2; Compose stays the dev path |
| Kafka | Strimzi self-managed | MSK, Confluent Cloud | cost/control; managed kept as fallback |

**Consequences:** the Python reference implementation of the stream jobs lets the whole platform run and be
evaluated on a laptop without a JVM; the Flink deployment shares its parsing code.
