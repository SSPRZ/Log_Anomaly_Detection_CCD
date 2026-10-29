"""Integration tests against real Kafka and ClickHouse (CI stage 4).

By default Testcontainers starts throwaway brokers. To reuse an already running
stack (e.g. ``make up``), point the tests at it instead:

    LAP_IT_KAFKA=localhost:9094 LAP_IT_CLICKHOUSE=localhost:8123:default:lap pytest tests/integration

Skipped automatically when neither is possible.
"""
import os
import time
import uuid

import pytest

EXT_KAFKA = os.environ.get("LAP_IT_KAFKA")
EXT_CH = os.environ.get("LAP_IT_CLICKHOUSE")  # host:port:user:password


def _docker_ok() -> bool:
    try:
        import docker

        docker.from_env().ping()
        return True
    except Exception:
        return False


if not (EXT_KAFKA and EXT_CH):
    pytest.importorskip("testcontainers.kafka")
    pytestmark = pytest.mark.skipif(not _docker_ok(), reason="Docker daemon not available")


@pytest.fixture(scope="module")
def kafka():
    if EXT_KAFKA:
        yield EXT_KAFKA
        return
    from testcontainers.kafka import KafkaContainer

    with KafkaContainer("confluentinc/cp-kafka:7.7.1") as k:
        yield k.get_bootstrap_server()


@pytest.fixture(scope="module")
def clickhouse():
    if EXT_CH:
        host, port, user, password = EXT_CH.split(":")
        yield {"host": host, "port": int(port), "username": user, "password": password, "compress": False}
        return
    from testcontainers.clickhouse import ClickHouseContainer

    with ClickHouseContainer("clickhouse/clickhouse-server:24.8") as c:
        yield {"host": c.get_container_host_ip(), "port": int(c.get_exposed_port(8123)),
               "username": c.username, "password": c.password}


def test_kafka_bus_roundtrip_with_manual_commits(kafka, cfg):
    from confluent_kafka.admin import AdminClient, NewTopic

    from common.bus import KafkaBus

    topic, group = f"it.{uuid.uuid4().hex[:8]}", f"it-{uuid.uuid4().hex[:8]}"
    admin = AdminClient({"bootstrap.servers": kafka})
    admin.create_topics([NewTopic(topic, 3, 1)])[topic].result(30)
    cfg["bus"]["kafka"]["bootstrap_servers"] = kafka
    bus = KafkaBus(cfg)
    for i in range(30):
        bus.produce(topic, {"i": i}, key=f"k{i % 3}")
    bus.flush()
    c = bus.consumer(group, [topic])
    got, deadline = [], time.time() + 30
    while len(got) < 30 and time.time() < deadline:
        got.extend(c.poll(100, 1.0))
    c.commit()
    assert sorted(r.value["i"] for r in got) == list(range(30))
    for k in ("k0", "k1", "k2"):
        seq = [r.value["i"] for r in got if r.key == k]
        assert seq == sorted(seq)
    c.close()
    admin.delete_topics([topic])


def test_clickhouse_store_roundtrip(clickhouse, cfg):
    from storage.store import ClickHouseStore
    from tests.conftest import parsed

    db = f"lap_it_{uuid.uuid4().hex[:8]}"
    cfg["storage"]["clickhouse"].update(clickhouse, database=db)
    s = ClickHouseStore(cfg)  # creates the database and applies storage/clickhouse/ddl
    now = time.time()
    s.insert_logs([parsed(ts=now, msg="Timeout calling bank-gateway")])
    s.upsert_features([{"service": "payments", "window_start": now, "window_end": now + 60, "total_count": 3,
                        "template_hist": [0] * 64, "template_counts": {}, "top_templates": []}])
    assert len(s.search_logs(text="Timeout")) == 1
    assert s.features().total_count.iloc[0] == 3
    s.upsert_alert({"alert_id": "al-1", "service": "payments", "severity": "P2", "status": "open", "opened_at": now,
                    "updated_at": now, "first_window": now, "last_window": now, "score": 0.8})
    s.add_feedback("al-1", "real", "it")
    assert s.alerts().alert_id.tolist() == ["al-1"] and s.feedback().verdict.tolist() == ["real"]
    s.client.command(f"DROP DATABASE {db}")
