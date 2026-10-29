"""Redaction rules tested against known samples (Section 7: 'redaction rules unit-tested')."""
import pytest

from stream.redaction import redact_text, redact_value


@pytest.mark.parametrize("raw, expected", [
    ("Creating order ord-1 for customer alice.42@example.com", "Creating order ord-1 for customer <EMAIL>"),
    ("Charging card 4111 1111 1111 1111 amount 9.99", "Charging card <CARD> amount 9.99"),
    ("Charging card 4111111111111111 now", "Charging card <CARD> now"),
    ("card 4111-1111-1111-1111", "card <CARD>"),
    ("Received POST /v1/checkout from 203.0.113.7", "Received POST /v1/checkout from <IP>"),
    ("Refreshed auth token tok_abcdefghijklmnopqrstuvwx for provider ses",
     "Refreshed auth token <TOKEN> for provider ses"),
    ("Authorization: Bearer abc.def-ghi_jkl123", "Authorization: Bearer <TOKEN>"),
    ("login password=hunter2 user=bob", "login password=<TOKEN> user=bob"),
    ("api_key: 'sk-test-123'", "api_key: <TOKEN>"),
    ("jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U ok",
     "jwt <TOKEN> ok"),
    ("key AKIAIOSFODNN7EXAMPLE", "key <TOKEN>"),
])
def test_known_samples_are_redacted(raw, expected):
    out, n = redact_text(raw)
    assert out == expected
    assert n >= 1


@pytest.mark.parametrize("safe", [
    "Payment authorized for order ord-393380 in 103 ms",
    "Completed POST /v1/checkout status=504 in 9012 ms",
    "Starting payments v1.4.0 on port 8080",
    "Timeout calling bank-gateway after 3000 ms (attempt 3/3)",
    "GC pause of 640 ms (heap 91% used)",
])
def test_ordinary_messages_are_untouched(safe):
    out, n = redact_text(safe)
    assert out == safe and n == 0


def test_attributes_are_redacted_recursively():
    attrs = {"latency_ms": 12, "email": "x@y.com", "nested": {"client_ip": "10.1.2.3", "list": ["a@b.io", 3]}}
    out, n = redact_value(attrs)
    assert out["latency_ms"] == 12
    assert out["email"] == "<REDACTED>"
    assert out["nested"]["client_ip"] == "<IP>"
    assert out["nested"]["list"] == ["<EMAIL>", 3]
    assert n == 3
