from stream.drain import Drain, mask


def test_mask_numbers_hex_and_ids():
    assert mask("Order ord-001234 confirmed") == "Order ord-<*> confirmed"
    assert mask("trace 4bf92f3577b34da6 took 12.5 ms") == "trace <*> took <*> ms"


def test_messages_with_variables_share_a_template():
    d = Drain("payments")
    c1, created1 = d.add("Timeout calling bank-gateway after 3000 ms (attempt 3/3)", 1.0)
    c2, created2 = d.add("Timeout calling bank-gateway after 2500 ms (attempt 2/3)", 2.0)
    assert created1 and not created2
    assert c1.id == c2.id
    assert c2.template == "Timeout calling bank-gateway after <*> ms (attempt <*>/<*>)"
    assert c2.size == 2 and c2.last_seen == 2.0


def test_word_variables_generalise():
    d = Drain("notifications")
    a, _ = d.add("Sent confirmation email to <EMAIL> via sendgrid")
    b, created = d.add("Sent confirmation email to <EMAIL> via ses")
    assert a.id == b.id and not created
    assert b.template.endswith("via <*>")


def test_different_messages_get_different_templates():
    d = Drain("orders")
    a, _ = d.add("Order ord-1 confirmed")
    b, _ = d.add("NullPointerException in OrderValidator.validate(OrderValidator.java:212)")
    assert a.id != b.id


def test_ids_are_stable_across_instances_and_restore():
    d1, d2 = Drain("svc"), Drain("svc")
    a, _ = d1.add("Health check passed (uptime 12s)")
    b, _ = d2.add("Health check passed (uptime 99s)")
    assert a.id == b.id  # derived from namespace + template, not insertion order
    d3 = Drain("svc")
    d3.restore(d1.snapshot())
    c, created = d3.add("Health check passed (uptime 5s)")
    assert c.id == a.id and not created
    assert d3.match("Health check passed (uptime 7s)").id == a.id


def test_namespaces_isolate_services():
    a, _ = Drain("a").add("Health check passed")
    b, _ = Drain("b").add("Health check passed")
    assert a.id != b.id
