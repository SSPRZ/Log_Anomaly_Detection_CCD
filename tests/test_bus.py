from common.bus import LocalBus, partition_for


def test_per_key_ordering_and_consumer_groups():
    bus = LocalBus({"t": 4})
    for i in range(20):
        bus.produce("t", {"i": i}, key=f"k{i % 3}")
    c1 = bus.consumer("g1", ["t"])
    got = []
    while batch := c1.poll(7):
        got.extend(batch)
    assert len(got) == 20
    for k in ("k0", "k1", "k2"):
        seq = [r.value["i"] for r in got if r.key == k]
        assert seq == sorted(seq)  # ordered within a key's partition
    assert len({r.partition for r in got if r.key == "k1"}) == 1
    c2 = bus.consumer("g2", ["t"])
    assert sum(len(b) for b in iter(lambda: c2.poll(100), [])) == 20  # independent group


def test_commit_and_resume_at_least_once():
    bus = LocalBus({"t": 1})
    for i in range(5):
        bus.produce("t", {"i": i}, key="a")
    c = bus.consumer("g", ["t"])
    c.poll(3)
    c.commit()
    c.poll(2)            # processed but not committed -> redelivered to a new instance
    c2 = bus.consumer("g", ["t"])
    assert [r.value["i"] for r in c2.poll(10)] == [3, 4]
    assert c2.lag() == 2


def test_latest_offset_reset_and_replay():
    bus = LocalBus({"t": 2})
    for i in range(6):
        bus.produce("t", {"i": i}, key=str(i))
    late = bus.consumer("late", ["t"], from_beginning=False)
    assert late.poll(10) == []
    bus.produce("t", {"i": 99}, key="x")
    assert [r.value["i"] for r in late.poll(10)] == [99]
    late.commit()
    bus.reset_offsets("late", "t", 0)  # replay history (e.g. after a parser fix)
    assert len(bus.consumer("late", ["t"]).poll(100)) == 7


def test_truncation_keeps_offsets_monotonic():
    bus = LocalBus({"t": 1})
    c = bus.consumer("g", ["t"])
    for i in range(10):
        bus.produce("t", {"i": i}, key="a")
    c.poll(100)
    c.commit()
    bus.truncate_consumed()
    bus.produce("t", {"i": 10}, key="a")
    r = c.poll(10)
    assert [x.value["i"] for x in r] == [10] and r[0].offset == 10
    assert bus.end_offsets("t") == [11]


def test_partitioner_is_stable():
    assert partition_for("payments", 24) == partition_for("payments", 24)
    assert partition_for(None, 24) == 0
