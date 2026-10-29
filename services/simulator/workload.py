"""Workload model of the simulated e-commerce system.

Five services (gateway, orders, payments, inventory, notifications) handle
checkout and catalogue requests with daily + weekly seasonality. Every request
is a trace whose log lines span several services, so the sequence model has
real workflows to learn. Background chatter (health checks, slow queries, cache
stats, token refreshes), benign deploys and realistic PII (e-mails, card
numbers, IPs, tokens) are included so redaction, template mining and
false-positive control are exercised.

``Shop.minute(t0)`` returns every record whose *event time* falls in
[t0, t0+60) together with a simulated shipping delay; ``EventStream`` turns
that into arrival order (slightly out of order, a few late records), which is
what Fluent Bit -> Kafka delivers in reality.
"""
from __future__ import annotations

import hashlib
import heapq
import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator

import yaml

from common.timeutil import iso
from services.simulator.faults import FaultSchedule

TOPOLOGY = Path(__file__).resolve().parents[2] / "config" / "topology.yaml"

FIRST = ["alice", "bob", "carol", "dave", "erin", "frank", "grace", "heidi", "ivan", "judy", "mallory", "trent"]
DOMAINS = ["example.com", "mail.test", "shop.example.org"]
SKUS = ["SKU-%05d" % i for i in range(100, 160)]
TABLES = ["orders", "order_items", "stock_levels", "customers", "ledger"]
PROVIDERS = ["sendgrid", "ses", "mailgun"]
FIELDS = ["email", "postcode", "quantity", "card_expiry", "phone"]
DECLINES = ["insufficient funds", "card expired", "do not honor"]

BAD_DEPLOY_ERRORS = {
    "orders": "NullPointerException in OrderValidator.validate(OrderValidator.java:212)",
    "payments": "KeyError: 'currency' in handler charge_card",
    "inventory": "IllegalStateException: reservation ledger out of sync for sku {sku}",
    "gateway": "TypeError: cannot read property 'headers' of undefined in authMiddleware",
    "notifications": "TemplateNotFound: order_confirmation_v3.html",
}


def _hex(rng: random.Random, n: int) -> str:
    return "%0*x" % (n, rng.getrandbits(4 * n))


def _pod(service: str, i: int) -> str:
    h = hashlib.md5(f"{service}-{i}".encode()).hexdigest()
    return f"{service}-{h[:4]}-{h[4:7]}"


@dataclass(order=True)
class Pending:
    arrival: float
    seq: int
    record: dict = field(compare=False)


class Shop:
    def __init__(self, faults: FaultSchedule | None = None, seed: int = 42, base_rpm: float = 24.0,
                 env: str = "prod", topology_path: Path = TOPOLOGY, benign_deploys: bool = True,
                 on_deploy: Callable[[dict], None] | None = None):
        self.rng = random.Random(seed)
        self.faults = faults or FaultSchedule()
        self.base_rpm = base_rpm
        self.env = env
        topo = yaml.safe_load(open(topology_path, encoding="utf-8"))
        self.services = list(topo["services"])
        self.hosts = {s: [_pod(s, i) for i in range(topo["services"][s]["hosts"])] for s in self.services}
        self.versions = {s: "v1.%d.0" % self.rng.randint(2, 9) for s in self.services}
        self.benign_deploys = benign_deploys
        self.on_deploy = on_deploy
        self.next_deploy = {}
        self._deployed_faults: set[str] = set()
        self._rolling: dict[str, tuple[float, float]] = {}  # service -> (start, end) of rolling restart
        self.uptime_start = {h: 0.0 for hs in self.hosts.values() for h in hs}

    # ------------------------------------------------------------------ helpers
    def rate_rpm(self, ts: float) -> float:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        h = dt.hour + dt.minute / 60.0
        daily = 0.30 + 0.70 * 0.5 * (1 - math.cos(2 * math.pi * (h - 4) / 24))  # trough 04:00, peak 16:00
        weekly = 0.70 if dt.weekday() >= 5 else 1.0
        return self.base_rpm * daily * weekly

    def _email(self) -> str:
        return f"{self.rng.choice(FIRST)}.{self.rng.randint(1, 999)}@{self.rng.choice(DOMAINS)}"

    def _ip(self) -> str:
        r = self.rng
        return f"{r.randint(11, 220)}.{r.randint(0, 255)}.{r.randint(0, 255)}.{r.randint(1, 254)}"

    def _card(self) -> str:
        groups = ["4%03d" % self.rng.randint(0, 999)] + ["%04d" % self.rng.randint(0, 9999) for _ in range(3)]
        return " ".join(groups) if self.rng.random() < 0.5 else "".join(groups)

    def _lat(self, median: float, sigma: float = 0.35) -> int:
        return max(1, int(self.rng.lognormvariate(math.log(median), sigma)))

    def _host_up(self, service: str, host: str, ts: float) -> bool:
        f = self.faults.first(service, "crash_loop", ts)
        if f:
            up, down = f.p("up_s"), f.p("down_s")
            idx = self.hosts[service].index(host)
            phase = (ts - f.start + idx * (up + down) / len(self.hosts[service])) % (up + down)
            if phase >= up:
                return False
        roll = self._rolling.get(service)
        if roll and roll[0] <= ts < roll[1]:
            idx = self.hosts[service].index(host)
            slot = (roll[1] - roll[0]) / len(self.hosts[service])
            if roll[0] + idx * slot <= ts < roll[0] + idx * slot + 10:
                return False
        return True

    def _up_hosts(self, service: str, ts: float) -> list[str]:
        return [h for h in self.hosts[service] if self._host_up(service, h, ts)]

    def _silent(self, service: str, ts: float) -> bool:
        return self.faults.first(service, "silent", ts) is not None

    def _latency_mult(self, service: str, ts: float) -> float:
        f = self.faults.first(service, "memory_leak", ts)
        return 1.0 + f.p("growth") * f.progress(ts) ** 1.5 if f else 1.0

    # ------------------------------------------------------------------ emission
    def _rec(self, out: list, service: str, level: str, msg: str, ts: float, host: str | None = None,
             trace_id: str | None = None, **attrs) -> None:
        if self._silent(service, ts):
            return
        if host is None:
            ups = self._up_hosts(service, ts)
            if not ups:
                return
            host = self.rng.choice(ups)
        elif not self._host_up(service, host, ts):
            return
        rec = {"ts": iso(ts), "service": service, "env": self.env, "level": level, "host": host, "msg": msg}
        if trace_id:
            rec["trace_id"] = trace_id
        if attrs:
            rec["attrs"] = attrs
        out.append(rec)

    # ------------------------------------------------------------------ requests
    def _checkout(self, out: list, t: float) -> None:
        r, R = self.rng, self._rec
        tid = _hex(r, 16)
        oid = "ord-%06d" % r.randint(0, 999999)
        email, ip = self._email(), self._ip()
        t_gw = t
        if not self._up_hosts("gateway", t):
            return
        R(out, "gateway", "INFO", f"Received POST /v1/checkout from {ip}", t, trace_id=tid, endpoint="/v1/checkout")
        if r.random() < 0.03:
            fld = r.choice(FIELDS)
            R(out, "gateway", "WARN", f"Validation failed for field {fld}: invalid format", t + 0.004,
              trace_id=tid, endpoint="/v1/checkout", status=400)
            R(out, "gateway", "WARN", f"Completed POST /v1/checkout status=400 in {self._lat(6)} ms", t + 0.006,
              trace_id=tid, endpoint="/v1/checkout", status=400, latency_ms=self._lat(6))
            return
        t += 0.005 + r.random() * 0.01
        status, failed = 200, False
        mult = self._latency_mult("gateway", t)

        if not self._up_hosts("orders", t):
            R(out, "gateway", "ERROR", "Upstream orders unavailable: connection refused", t, trace_id=tid,
              endpoint="/v1/checkout", status=503)
            status, failed = 503, True
        else:
            R(out, "orders", "INFO", f"Creating order {oid} for customer {email}", t, trace_id=tid)
            bad = self.faults.first("orders", "bad_deploy", t)
            if bad and r.random() < bad.p("fraction"):
                R(out, "orders", "ERROR", BAD_DEPLOY_ERRORS["orders"], t + 0.002, trace_id=tid)
                status, failed = 500, True
        if not failed:
            t += 0.01
            n, sku = r.randint(1, 4), r.choice(SKUS)
            if not self._up_hosts("inventory", t):
                R(out, "orders", "ERROR", "Upstream inventory unavailable: connection refused", t, trace_id=tid)
                status, failed = 503, True
            else:
                lat = int(self._lat(18) * self._latency_mult("inventory", t))
                bad = self.faults.first("inventory", "bad_deploy", t)
                if bad and r.random() < bad.p("fraction"):
                    R(out, "inventory", "ERROR", BAD_DEPLOY_ERRORS["inventory"].format(sku=sku), t, trace_id=tid)
                    status, failed = 500, True
                else:
                    R(out, "inventory", "INFO", f"Reserved {n} units of {sku} for order {oid}", t + lat / 1000,
                      trace_id=tid, latency_ms=lat)
                t += lat / 1000
        skip = self.faults.first("orders", "skip_step", t)
        if not failed and not (skip and r.random() < skip.p("fraction")):
            t += 0.005
            if not self._up_hosts("payments", t):
                R(out, "orders", "ERROR", "Upstream payments unavailable: connection refused", t, trace_id=tid)
                status, failed = 503, True
            else:
                amount = "%.2f" % (r.uniform(5, 400))
                R(out, "payments", "INFO", f"Charging card {self._card()} amount {amount} USD for order {oid}", t,
                  trace_id=tid)
                timeout = self.faults.first("payments", "downstream_timeout", t)
                bad = self.faults.first("payments", "bad_deploy", t)
                if timeout and r.random() < timeout.p("fraction"):
                    for attempt in (1, 2):
                        t += 3.0
                        R(out, "payments", "WARN", f"Retrying call to bank-gateway (attempt {attempt}/3)", t,
                          trace_id=tid, endpoint="/v1/charge")
                    t += 3.0
                    lat = 3000 + r.randint(5, 40)
                    R(out, "payments", "ERROR", "Timeout calling bank-gateway after 3000 ms (attempt 3/3)", t,
                      trace_id=tid, endpoint="/v1/charge", latency_ms=lat, status=504)
                    R(out, "orders", "ERROR", f"Order {oid} failed: payment service timeout", t + 0.002, trace_id=tid)
                    status, failed = 504, True
                elif bad and r.random() < bad.p("fraction"):
                    R(out, "payments", "ERROR", BAD_DEPLOY_ERRORS["payments"], t + 0.001, trace_id=tid,
                      endpoint="/v1/charge", status=500)
                    status, failed = 500, True
                elif r.random() < 0.02:
                    lat = self._lat(140)
                    t += lat / 1000
                    R(out, "payments", "WARN", f"Card declined for order {oid}: {r.choice(DECLINES)}", t,
                      trace_id=tid, endpoint="/v1/charge", latency_ms=lat, status=402)
                    R(out, "orders", "WARN", f"Order {oid} failed: payment declined", t + 0.002, trace_id=tid)
                    status, failed = 402, True
                else:
                    lat = int(self._lat(140) * self._latency_mult("payments", t))
                    t += lat / 1000
                    R(out, "payments", "INFO", f"Payment authorized for order {oid} in {lat} ms", t,
                      trace_id=tid, endpoint="/v1/charge", latency_ms=lat, status=200)
        if not failed:
            t += 0.004
            R(out, "orders", "INFO", f"Order {oid} confirmed", t, trace_id=tid)
            t_n = t + r.uniform(0.5, 3.0)  # async notification
            bad = self.faults.first("notifications", "bad_deploy", t_n)
            if bad and r.random() < bad.p("fraction"):
                R(out, "notifications", "ERROR", BAD_DEPLOY_ERRORS["notifications"], t_n, trace_id=tid)
            else:
                lat = self._lat(80)
                R(out, "notifications", "INFO", f"Sent confirmation email to {email} via {r.choice(PROVIDERS)}",
                  t_n, trace_id=tid, latency_ms=lat)
        total = int(((t - t_gw) * 1000 + self._lat(12)) * mult)
        level = "ERROR" if status >= 500 else ("WARN" if status >= 400 else "INFO")
        R(out, "gateway", level, f"Completed POST /v1/checkout status={status} in {total} ms", t + 0.003,
          trace_id=tid, endpoint="/v1/checkout", status=status, latency_ms=total)

    def _browse(self, out: list, t: float) -> None:
        r, R = self.rng, self._rec
        tid = _hex(r, 16)
        if not self._up_hosts("gateway", t):
            return
        page = r.randint(1, 20)
        R(out, "gateway", "INFO", f"Received GET /v1/products?page={page} from {self._ip()}", t, trace_id=tid,
          endpoint="/v1/products")
        mult = self._latency_mult("gateway", t)
        if not self._up_hosts("inventory", t):
            R(out, "gateway", "ERROR", "Upstream inventory unavailable: connection refused", t + 0.01, trace_id=tid,
              endpoint="/v1/products", status=503)
            status = 503
            lat = 15
        else:
            lat = int(self._lat(25) * self._latency_mult("inventory", t))
            R(out, "inventory", "INFO", f"Fetched {r.randint(10, 50)} products from catalog in {lat} ms",
              t + lat / 1000, trace_id=tid, latency_ms=lat)
            status = 200
        total = int((lat + self._lat(8)) * mult)
        level = "ERROR" if status >= 500 else "INFO"
        R(out, "gateway", level, f"Completed GET /v1/products status={status} in {total} ms", t + total / 1000,
          trace_id=tid, endpoint="/v1/products", status=status, latency_ms=total)

    # ------------------------------------------------------------------ background
    def _background(self, out: list, t0: float) -> None:
        r, R = self.rng, self._rec
        for svc in self.services:
            for i, host in enumerate(self.hosts[svc]):
                for k in (0, 30):
                    ts = t0 + k + (i * 3.7) % 30
                    up = int(ts - self.uptime_start.get(host, 0.0))
                    R(out, svc, "DEBUG", f"Health check passed (uptime {up}s)", ts, host=host)
                leak = self.faults.first(svc, "memory_leak", t0)
                if leak:
                    prog = leak.progress(t0)
                    for _ in range(self._poisson(0.3 + prog * 6)):
                        R(out, svc, "WARN", f"GC pause of {int(40 + prog * 900 * r.random())} ms "
                                            f"(heap {int(60 + prog * 38)}% used)", t0 + r.random() * 60, host=host)
                    if prog > 0.85 and r.random() < 0.3:
                        R(out, svc, "ERROR", "OutOfMemoryError: Java heap space", t0 + r.random() * 60, host=host)
                crash = self.faults.first(svc, "crash_loop", t0)
                if crash:
                    up, down = crash.p("up_s"), crash.p("down_s")
                    period = up + down
                    offset = i * period / len(self.hosts[svc])
                    k = math.floor((t0 - crash.start + offset) / period)
                    while True:
                        start = crash.start - offset + k * period
                        if start >= t0 + 60:
                            break
                        if start >= t0 and start >= crash.start:
                            self.uptime_start[host] = start
                            R(out, svc, "INFO", f"Starting {svc} {self.versions[svc]} on port 8080", start + 0.5,
                              host=host)
                            R(out, svc, "WARN", "Liveness probe failed: connection refused", start + 1.0, host=host)
                            stop = start + up - 0.5
                            if stop < t0 + 60:
                                R(out, svc, "FATAL", "Process exited with code 137 (OOMKilled)", stop, host=host)
                        k += 1
            if svc in ("orders", "inventory"):
                for _ in range(self._poisson(0.25)):
                    R(out, svc, "WARN", f"Slow query on table {r.choice(TABLES)} took {self._lat(900)} ms",
                      t0 + r.random() * 60)
            if svc in ("gateway", "inventory"):
                R(out, svc, "INFO", f"Cache hit ratio {r.uniform(0.82, 0.97):.2f} over last 60 s", t0 + 59.5)
            if svc == "payments":
                R(out, svc, "INFO", f"Connection pool stats: active={r.randint(2, 12)} idle={r.randint(4, 20)}",
                  t0 + 45)
            if svc == "notifications" and r.random() < 0.2:
                R(out, svc, "INFO", f"Refreshed auth token tok_{_hex(r, 24)} for provider {r.choice(PROVIDERS)}",
                  t0 + r.random() * 60)
            if r.random() < 0.015:
                R(out, svc, "ERROR", "Failed to publish metric batch: connection reset by peer", t0 + r.random() * 60)

    def _deploys(self, out: list, t0: float) -> None:
        r = self.rng
        for svc in self.services:
            # injected bad deploy: announce a deploy at fault start
            for f in self.faults.active(svc, t0) + self.faults.active(svc, t0 + 59.999):
                # real time: a fault injected through the API mid-minute starts after that minute was
                # generated, so announce any active bad deploy that has not been announced yet
                if f.kind == "bad_deploy" and f.id not in self._deployed_faults and f.start < t0 + 60:
                    self._deployed_faults.add(f.id)
                    self._announce_deploy(out, svc, f.p("version"), max(f.start, t0))
            if not self.benign_deploys:
                continue
            nxt = self.next_deploy.setdefault(svc, t0 + r.uniform(6, 60) * 3600)
            if t0 <= nxt < t0 + 60:
                v = self.versions[svc].split(".")
                self._announce_deploy(out, svc, f"{v[0]}.{int(v[1]) + 1}.0", nxt)
                self.next_deploy[svc] = nxt + r.uniform(24, 60) * 3600

    def _announce_deploy(self, out: list, svc: str, version: str, ts: float) -> None:
        self.versions[svc] = version
        sha = _hex(self.rng, 7)
        self._rolling[svc] = (ts, ts + 10 * len(self.hosts[svc]) + 5)
        self._rec(out, svc, "INFO", f"Deployed version {version} (commit {sha})", ts, host=self.hosts[svc][0])
        for i, h in enumerate(self.hosts[svc]):
            st = ts + i * 10 + 10
            self.uptime_start[h] = st
            self._rec(out, svc, "INFO", f"Starting {svc} {version} on port 8080", st, host=h)
        if self.on_deploy:
            self.on_deploy({"ts": ts, "service": svc, "version": version, "commit": sha})

    def _poisson(self, lam: float) -> int:
        if lam <= 0:
            return 0
        if lam > 30:
            return max(0, int(self.rng.gauss(lam, math.sqrt(lam))))
        L, k, p = math.exp(-lam), 0, 1.0
        while True:
            p *= self.rng.random()
            if p <= L:
                return k
            k += 1

    # ------------------------------------------------------------------ public
    def minute(self, t0: float) -> list[dict]:
        out: list[dict] = []
        self._deploys(out, t0)
        rpm = self.rate_rpm(t0) * self.rng.gauss(1.0, 0.06)
        for _ in range(self._poisson(rpm * 0.7)):
            self._checkout(out, t0 + self.rng.random() * 57)
        for _ in range(self._poisson(rpm * 0.3)):
            self._browse(out, t0 + self.rng.random() * 58)
        self._background(out, t0)
        return out

    def ship_delay(self) -> float:
        """Fluent Bit batching + network: mostly sub-second, rarely 10-25 s (late but within lateness)."""
        if self.rng.random() < 0.001:
            return self.rng.uniform(10, 25)
        return min(4.0, self.rng.expovariate(2.0))


def canary_records(t0: float, interval: int = 30, env: str = "prod") -> list[dict]:
    return [{"ts": iso(t0 + k), "service": "canary", "env": env, "level": "INFO",
             "host": "canary-0", "msg": "canary heartbeat"} for k in range(0, 60, interval)]


class EventStream:
    """Turn per-minute generation into arrival-ordered records (what Kafka sees)."""

    def __init__(self, shop: Shop, canary_interval: int = 30):
        self.shop = shop
        self.heap: list[Pending] = []
        self.seq = 0
        self.canary_interval = canary_interval

    def _push(self, rec: dict, event_ts: float) -> None:
        self.seq += 1
        heapq.heappush(self.heap, Pending(event_ts + self.shop.ship_delay(), self.seq, rec))

    def generate(self, start: float, end: float) -> Iterator[tuple[float, dict]]:
        """Yield (arrival_time, record) for event times in [start, end)."""
        from common.timeutil import parse_ts

        t = start - (start % 60)
        while t < end:
            for rec in self.shop.minute(t):
                self._push(rec, parse_ts(rec["ts"]))
            if self.canary_interval:
                for rec in canary_records(t, self.canary_interval, self.shop.env):
                    self._push(rec, parse_ts(rec["ts"]))
            t += 60
            while self.heap and self.heap[0].arrival < t:
                p = heapq.heappop(self.heap)
                yield p.arrival, p.record
        while self.heap:
            p = heapq.heappop(self.heap)
            yield p.arrival, p.record
