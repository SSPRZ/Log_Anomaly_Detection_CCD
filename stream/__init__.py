"""Stream processing: parse-enrich and window-features jobs (Section 4.3).

The Python classes here are the reference implementation used by the lite
stack and by the Kafka-backed `stream-processor` container. stream/flink/
holds the PyFlink / Flink SQL deployment of the same logic for the cluster.
"""
