"""PII redaction applied before anything reaches storage (Section 7).

Rules are ordered: more specific patterns first so, for example, a JWT is
labelled <TOKEN> rather than partially matched by the IP or card rule.
The rule set is unit-tested against known samples in tests/test_redaction.py.
"""
from __future__ import annotations

import re
from typing import Any

RULES: list[tuple[str, re.Pattern, str]] = [
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\b"), "<TOKEN>"),
    ("bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"), "Bearer <TOKEN>"),
    ("kv_secret", re.compile(r"(?i)\b(password|passwd|pwd|secret|api[_-]?key|access[_-]?token|token)"
                             r"(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;&]+)"), r"\1\2<TOKEN>"),
    ("prefixed_token", re.compile(r"\b(?:tok|sk|pk|rk|ghp|xox[abpr])_[A-Za-z0-9]{12,}\b"), "<TOKEN>"),
    ("aws_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "<TOKEN>"),
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b"), "<EMAIL>"),
    # 13-19 digits, optionally grouped by spaces or dashes (card-like numbers)
    ("card", re.compile(r"(?<![\d.])(?:\d[ -]?){12,18}\d(?![\d.])"), "<CARD>"),
    ("ipv4", re.compile(r"(?<![\d.])(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?![\d.])"),
     "<IP>"),
    ("ipv6", re.compile(r"\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b"), "<IP>"),
]

# attribute keys whose values are always dropped regardless of content
SENSITIVE_KEYS = {"password", "passwd", "secret", "token", "authorization", "cookie", "card", "card_number",
                  "ssn", "email", "api_key", "apikey"}


def redact_text(text: str) -> tuple[str, int]:
    """Return (redacted_text, number_of_replacements)."""
    total = 0
    for _name, pattern, repl in RULES:
        text, n = pattern.subn(repl, text)
        total += n
    return text, total


def redact_value(value: Any) -> tuple[Any, int]:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        out, n = {}, 0
        for k, v in value.items():
            if str(k).lower() in SENSITIVE_KEYS:
                out[k] = "<REDACTED>"
                n += 1
            else:
                out[k], m = redact_value(v)
                n += m
        return out, n
    if isinstance(value, list):
        out_l, n = [], 0
        for v in value:
            r, m = redact_value(v)
            out_l.append(r)
            n += m
        return out_l, n
    return value, 0
