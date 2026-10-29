"""Hot + analytical store (Section 4.4).

``ClickHouseStore`` is the production backend (MergeTree tables, TTLs, token
Bloom-filter index; DDL in storage/clickhouse/ddl). ``SQLiteStore`` implements
the same interface for the lite stack, tests and laptops without Docker. TTLs
are enforced by ``purge_ttl`` in SQLite and natively by ClickHouse.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Iterable

import pandas as pd

from common.config import resolve_path

FEATURE_COLUMNS = [
    "total_count", "count_delta_week", "distinct_hosts", "error_count", "warn_count", "error_ratio", "fatal",
    "unique_templates", "new_templates", "template_entropy", "p50_latency_ms", "p95_latency_ms",
    "p99_latency_ms", "ratio_5xx", "seconds_since_last",
]


def _j(v) -> str:
    return json.dumps(v, separators=(",", ":"), default=str)


class Store:
    def insert_logs(self, rows: list[dict]) -> None: ...
    def upsert_templates(self, rows: list[dict]) -> None: ...
    def templates(self) -> pd.DataFrame: ...
    def upsert_features(self, rows: list[dict]) -> None: ...
    def features(self, service: str | None = None, start: float | None = None, end: float | None = None,
                 with_payload: bool = False) -> pd.DataFrame: ...
    def insert_anomalies(self, rows: list[dict]) -> None: ...
    def anomalies(self, start: float | None = None, end: float | None = None, service: str | None = None,
                  only_anomalous: bool = False, limit: int | None = None) -> pd.DataFrame: ...
    def upsert_alert(self, row: dict) -> None: ...
    def alerts(self, start: float | None = None, end: float | None = None, limit: int | None = None) -> pd.DataFrame: ...
    def add_feedback(self, alert_id: str, verdict: str, user: str = "", comment: str = "",
                     ts: float | None = None) -> None: ...
    def feedback(self) -> pd.DataFrame: ...
    def insert_dlq(self, rows: list[dict]) -> None: ...
    def dlq(self, limit: int = 200) -> pd.DataFrame: ...
    def insert_deploys(self, rows: list[dict]) -> None: ...
    def deploys(self) -> pd.DataFrame: ...
    def search_logs(self, text: str = "", service: str | None = None, level: str | None = None,
                    start: float | None = None, end: float | None = None, template_id: str | None = None,
                    limit: int = 200) -> pd.DataFrame: ...
    def purge_ttl(self, now: float | None = None) -> None: ...
    def close(self) -> None: ...


# --------------------------------------------------------------------------- sqlite

SQLITE_DDL = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
CREATE TABLE IF NOT EXISTS logs (
  ts REAL, service TEXT, env TEXT, level TEXT, host TEXT, trace_id TEXT,
  template_id TEXT, msg TEXT, attrs TEXT);
CREATE INDEX IF NOT EXISTS logs_svc_ts ON logs(service, ts);
CREATE INDEX IF NOT EXISTS logs_tpl ON logs(template_id);
CREATE TABLE IF NOT EXISTS log_templates (
  template_id TEXT PRIMARY KEY, namespace TEXT, pattern TEXT, size INTEGER,
  first_seen REAL, last_seen REAL);
CREATE TABLE IF NOT EXISTS features_1m (
  service TEXT, window_start REAL, window_end REAL, revision INTEGER,
  total_count REAL, count_delta_week REAL, distinct_hosts REAL, error_count REAL, warn_count REAL,
  error_ratio REAL, fatal REAL, unique_templates REAL, new_templates REAL, template_entropy REAL,
  p50_latency_ms REAL, p95_latency_ms REAL, p99_latency_ms REAL, ratio_5xx REAL, seconds_since_last REAL,
  payload TEXT, PRIMARY KEY (service, window_start));
CREATE TABLE IF NOT EXISTS anomalies (
  service TEXT, window_start REAL, window_end REAL, score REAL, is_anomalous INTEGER,
  stats REAL, iforest REAL, sequence REAL, rule TEXT, top_drivers TEXT, explanation TEXT,
  model_version TEXT, shadow TEXT, emitted_at REAL, e2e_latency_s REAL,
  PRIMARY KEY (service, window_start));
CREATE INDEX IF NOT EXISTS anomalies_ws ON anomalies(window_start);
CREATE TABLE IF NOT EXISTS alerts (
  alert_id TEXT PRIMARY KEY, incident_id TEXT, service TEXT, severity TEXT, status TEXT,
  opened_at REAL, updated_at REAL, first_window REAL, last_window REAL, score REAL,
  dominant_template TEXT, payload TEXT, channels TEXT);
CREATE TABLE IF NOT EXISTS alert_feedback (
  alert_id TEXT, verdict TEXT, user TEXT, comment TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS dlq (ts REAL, stage TEXT, reason TEXT, record TEXT);
CREATE TABLE IF NOT EXISTS deploys (ts REAL, service TEXT, version TEXT, commit_sha TEXT);
"""


class SQLiteStore(Store):
    def __init__(self, path: str | Path, retention_days: dict | None = None):
        path = Path(path)
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.retention = retention_days or {"logs": 14, "features_1m": 90, "anomalies": 365}
        self._conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
        self._conn.executescript(SQLITE_DDL)
        self._lock = threading.RLock()

    def _many(self, sql: str, rows: Iterable[tuple]) -> None:
        rows = list(rows)
        if not rows:
            return
        with self._lock:
            self._conn.executemany(sql, rows)
            self._conn.commit()

    def _df(self, sql: str, params: tuple = ()) -> pd.DataFrame:
        with self._lock:
            return pd.read_sql_query(sql, self._conn, params=params)

    # logs / templates ---------------------------------------------------------
    def insert_logs(self, rows):
        self._many("INSERT INTO logs VALUES (?,?,?,?,?,?,?,?,?)",
                   ((r["ts"], r["service"], r["env"], r["level"], r.get("host", ""), r.get("trace_id", ""),
                     r["template_id"], r["msg"], _j(r.get("attrs") or {})) for r in rows))

    def upsert_templates(self, rows):
        self._many("""INSERT INTO log_templates VALUES (?,?,?,?,?,?)
                      ON CONFLICT(template_id) DO UPDATE SET pattern=excluded.pattern, size=excluded.size,
                      last_seen=MAX(last_seen, excluded.last_seen)""",
                   ((r["template_id"], r["namespace"], r["pattern"], r["size"], r["first_seen"], r["last_seen"])
                    for r in rows))

    def templates(self):
        return self._df("SELECT * FROM log_templates")

    # features -----------------------------------------------------------------
    def upsert_features(self, rows):
        cols = ["service", "window_start", "window_end", "revision"] + FEATURE_COLUMNS + ["payload"]
        ph = ",".join("?" * len(cols))
        self._many(f"INSERT OR REPLACE INTO features_1m ({','.join(cols)}) VALUES ({ph})",
                   ([r["service"], r["window_start"], r["window_end"], r.get("revision", 0)]
                    + [r.get(c) for c in FEATURE_COLUMNS]
                    + [_j({k: r.get(k) for k in ("template_hist", "template_counts", "top_templates")})]
                    for r in rows))

    def features(self, service=None, start=None, end=None, with_payload=False):
        cols = "*" if with_payload else ", ".join(["service", "window_start", "window_end"] + FEATURE_COLUMNS)
        q, p = f"SELECT {cols} FROM features_1m WHERE 1=1", []
        if service:
            q += " AND service = ?"; p.append(service)
        if start is not None:
            q += " AND window_start >= ?"; p.append(start)
        if end is not None:
            q += " AND window_start < ?"; p.append(end)
        df = self._df(q + " ORDER BY service, window_start", tuple(p))
        if with_payload and not df.empty:
            payload = df.pop("payload").map(json.loads)
            for k in ("template_hist", "template_counts", "top_templates"):
                df[k] = payload.map(lambda d, k=k: d.get(k))
        return df

    # anomalies ----------------------------------------------------------------
    def insert_anomalies(self, rows):
        self._many("INSERT OR REPLACE INTO anomalies VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   ((r["service"], r["window_start"], r["window_end"], r["score"], int(r["is_anomalous"]),
                     r["tiers"].get("stats"), r["tiers"].get("iforest"), r["tiers"].get("sequence"),
                     _j(r["tiers"].get("rule")), _j(r.get("top_drivers", [])), _j(r.get("explanation", {})),
                     r.get("model_version", ""), _j(r.get("shadow")), r.get("emitted_at"),
                     r.get("e2e_latency_s")) for r in rows))

    def anomalies(self, start=None, end=None, service=None, only_anomalous=False, limit=None):
        q, p = "SELECT * FROM anomalies WHERE 1=1", []
        if start is not None:
            q += " AND window_start >= ?"; p.append(start)
        if end is not None:
            q += " AND window_start < ?"; p.append(end)
        if service:
            q += " AND service = ?"; p.append(service)
        if only_anomalous:
            q += " AND is_anomalous = 1"
        q += " ORDER BY window_start DESC"
        if limit:
            q += f" LIMIT {int(limit)}"
        return self._df(q, tuple(p))

    # alerts / feedback ----------------------------------------------------------
    def upsert_alert(self, row):
        self._many("INSERT OR REPLACE INTO alerts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   [(row["alert_id"], row.get("incident_id"), row["service"], row["severity"], row["status"],
                     row["opened_at"], row["updated_at"], row["first_window"], row["last_window"], row["score"],
                     row.get("dominant_template", ""), _j(row.get("payload", {})), _j(row.get("channels", [])))])

    def alerts(self, start=None, end=None, limit=None):
        q, p = "SELECT * FROM alerts WHERE 1=1", []
        if start is not None:
            q += " AND first_window >= ?"; p.append(start)
        if end is not None:
            q += " AND first_window < ?"; p.append(end)
        q += " ORDER BY opened_at DESC"
        if limit:
            q += f" LIMIT {int(limit)}"
        return self._df(q, tuple(p))

    def add_feedback(self, alert_id, verdict, user="", comment="", ts=None):
        if verdict not in ("real", "false_alarm"):
            raise ValueError("verdict must be 'real' or 'false_alarm'")
        self._many("INSERT INTO alert_feedback VALUES (?,?,?,?,?)",
                   [(alert_id, verdict, user, comment, ts or time.time())])

    def feedback(self):
        return self._df("SELECT * FROM alert_feedback ORDER BY ts")

    # dlq / deploys --------------------------------------------------------------
    def insert_dlq(self, rows):
        self._many("INSERT INTO dlq VALUES (?,?,?,?)",
                   ((r.get("ts", time.time()), r["stage"], r["reason"],
                     r["record"] if isinstance(r["record"], str) else _j(r["record"])) for r in rows))

    def dlq(self, limit=200):
        return self._df(f"SELECT * FROM dlq ORDER BY ts DESC LIMIT {int(limit)}")

    def insert_deploys(self, rows):
        self._many("INSERT INTO deploys VALUES (?,?,?,?)",
                   ((r["ts"], r["service"], r.get("version", ""), r.get("commit", "")) for r in rows))

    def deploys(self):
        return self._df("SELECT * FROM deploys ORDER BY ts")

    # search -------------------------------------------------------------------
    def search_logs(self, text="", service=None, level=None, start=None, end=None, template_id=None, limit=200):
        q, p = "SELECT ts, service, level, host, trace_id, template_id, msg, attrs FROM logs WHERE 1=1", []
        if text:
            q += " AND msg LIKE ?"; p.append(f"%{text}%")
        if service:
            q += " AND service = ?"; p.append(service)
        if level:
            q += " AND level = ?"; p.append(level)
        if start is not None:
            q += " AND ts >= ?"; p.append(start)
        if end is not None:
            q += " AND ts < ?"; p.append(end)
        if template_id:
            q += " AND template_id = ?"; p.append(template_id)
        return self._df(q + f" ORDER BY ts DESC LIMIT {int(limit)}", tuple(p))

    def purge_ttl(self, now=None):
        now = now or time.time()
        with self._lock:
            self._conn.execute("DELETE FROM logs WHERE ts < ?", (now - self.retention["logs"] * 86400,))
            self._conn.execute("DELETE FROM features_1m WHERE window_start < ?",
                               (now - self.retention["features_1m"] * 86400,))
            self._conn.execute("DELETE FROM anomalies WHERE window_start < ?",
                               (now - self.retention["anomalies"] * 86400,))
            self._conn.commit()

    def close(self):
        with self._lock:
            self._conn.close()


# --------------------------------------------------------------------------- clickhouse


class ClickHouseStore(Store):
    """ClickHouse backend. Tables are created by storage/clickhouse/ddl/*.sql (see migrate())."""

    def __init__(self, cfg: dict):
        import clickhouse_connect  # optional dependency

        c = cfg["storage"]["clickhouse"]
        self.db = c["database"]
        conn = dict(host=c["host"], port=int(c["port"]), username=c["username"], password=c.get("password", ""),
                    compress=bool(c.get("compress", True)))
        admin = clickhouse_connect.get_client(**conn)
        admin.command(f"CREATE DATABASE IF NOT EXISTS {self.db}")
        self.client = clickhouse_connect.get_client(**conn, database=self.db)
        self._lock = threading.RLock()
        self.migrate()

    def migrate(self) -> None:
        ddl_dir = Path(__file__).resolve().parent / "clickhouse" / "ddl"
        for f in sorted(ddl_dir.glob("*.sql")):
            # drop comment lines first: a ";" inside a comment must not split statements
            lines = f.read_text(encoding="utf-8").splitlines()
            sql = "\n".join(ln for ln in lines if not ln.lstrip().startswith("--"))
            for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
                self.client.command(stmt)

    def _insert(self, table: str, cols: list[str], rows: list[list]) -> None:
        if rows:
            with self._lock:
                self.client.insert(table, rows, column_names=cols)

    def _df(self, sql: str, params: dict | None = None) -> pd.DataFrame:
        with self._lock:
            return self.client.query_df(sql, parameters=params or {})

    def insert_logs(self, rows):
        self._insert("logs", ["ts", "service", "env", "level", "host", "trace_id", "template_id", "msg", "attrs"],
                     [[r["ts"], r["service"], r["env"], r["level"], r.get("host", ""), r.get("trace_id", ""),
                       r["template_id"], r["msg"], _j(r.get("attrs") or {})] for r in rows])

    def upsert_templates(self, rows):
        self._insert("log_templates", ["template_id", "namespace", "pattern", "size", "first_seen", "last_seen"],
                     [[r["template_id"], r["namespace"], r["pattern"], r["size"], r["first_seen"], r["last_seen"]]
                      for r in rows])

    def templates(self):
        return self._df("SELECT * FROM log_templates FINAL")

    def upsert_features(self, rows):
        cols = ["service", "window_start", "window_end", "revision"] + FEATURE_COLUMNS + ["payload"]
        self._insert("features_1m", cols,
                     [[r["service"], r["window_start"], r["window_end"], r.get("revision", 0)]
                      + [r.get(c) for c in FEATURE_COLUMNS]
                      + [_j({k: r.get(k) for k in ("template_hist", "template_counts", "top_templates")})]
                      for r in rows])

    def features(self, service=None, start=None, end=None, with_payload=False):
        cols = "*" if with_payload else ", ".join(["service", "window_start", "window_end"] + FEATURE_COLUMNS)
        q, p = f"SELECT {cols} FROM features_1m FINAL WHERE 1=1", {}
        if service:
            q += " AND service = {service:String}"; p["service"] = service
        if start is not None:
            q += " AND window_start >= {start:Float64}"; p["start"] = start
        if end is not None:
            q += " AND window_start < {end:Float64}"; p["end"] = end
        df = self._df(q + " ORDER BY service, window_start", p)
        if with_payload and not df.empty:
            payload = df.pop("payload").map(json.loads)
            for k in ("template_hist", "template_counts", "top_templates"):
                df[k] = payload.map(lambda d, k=k: d.get(k))
        return df

    def insert_anomalies(self, rows):
        self._insert("anomalies",
                     ["service", "window_start", "window_end", "score", "is_anomalous", "stats", "iforest",
                      "sequence", "rule", "top_drivers", "explanation", "model_version", "shadow", "emitted_at",
                      "e2e_latency_s"],
                     [[r["service"], r["window_start"], r["window_end"], r["score"], int(r["is_anomalous"]),
                       r["tiers"].get("stats"), r["tiers"].get("iforest"), r["tiers"].get("sequence"),
                       _j(r["tiers"].get("rule")), _j(r.get("top_drivers", [])), _j(r.get("explanation", {})),
                       r.get("model_version", ""), _j(r.get("shadow")), r.get("emitted_at") or 0.0,
                       r.get("e2e_latency_s")] for r in rows])

    def anomalies(self, start=None, end=None, service=None, only_anomalous=False, limit=None):
        q, p = "SELECT * FROM anomalies FINAL WHERE 1=1", {}
        if start is not None:
            q += " AND window_start >= {start:Float64}"; p["start"] = start
        if end is not None:
            q += " AND window_start < {end:Float64}"; p["end"] = end
        if service:
            q += " AND service = {service:String}"; p["service"] = service
        if only_anomalous:
            q += " AND is_anomalous = 1"
        q += " ORDER BY window_start DESC" + (f" LIMIT {int(limit)}" if limit else "")
        return self._df(q, p)

    def upsert_alert(self, row):
        self._insert("alerts",
                     ["alert_id", "incident_id", "service", "severity", "status", "opened_at", "updated_at",
                      "first_window", "last_window", "score", "dominant_template", "payload", "channels"],
                     [[row["alert_id"], row.get("incident_id") or "", row["service"], row["severity"], row["status"],
                       row["opened_at"], row["updated_at"], row["first_window"], row["last_window"], row["score"],
                       row.get("dominant_template", ""), _j(row.get("payload", {})), _j(row.get("channels", []))]])

    def alerts(self, start=None, end=None, limit=None):
        q, p = "SELECT * FROM alerts FINAL WHERE 1=1", {}
        if start is not None:
            q += " AND first_window >= {start:Float64}"; p["start"] = start
        if end is not None:
            q += " AND first_window < {end:Float64}"; p["end"] = end
        q += " ORDER BY opened_at DESC" + (f" LIMIT {int(limit)}" if limit else "")
        return self._df(q, p)

    def add_feedback(self, alert_id, verdict, user="", comment="", ts=None):
        if verdict not in ("real", "false_alarm"):
            raise ValueError("verdict must be 'real' or 'false_alarm'")
        self._insert("alert_feedback", ["alert_id", "verdict", "user", "comment", "ts"],
                     [[alert_id, verdict, user, comment, ts or time.time()]])

    def feedback(self):
        return self._df("SELECT * FROM alert_feedback ORDER BY ts")

    def insert_dlq(self, rows):
        self._insert("dlq", ["ts", "stage", "reason", "record"],
                     [[r.get("ts", time.time()), r["stage"], r["reason"],
                       r["record"] if isinstance(r["record"], str) else _j(r["record"])] for r in rows])

    def dlq(self, limit=200):
        return self._df(f"SELECT * FROM dlq ORDER BY ts DESC LIMIT {int(limit)}")

    def insert_deploys(self, rows):
        self._insert("deploys", ["ts", "service", "version", "commit_sha"],
                     [[r["ts"], r["service"], r.get("version", ""), r.get("commit", "")] for r in rows])

    def deploys(self):
        return self._df("SELECT * FROM deploys ORDER BY ts")

    def search_logs(self, text="", service=None, level=None, start=None, end=None, template_id=None, limit=200):
        q, p = "SELECT ts, service, level, host, trace_id, template_id, msg, attrs FROM logs WHERE 1=1", {}
        if text:  # served by the tokenbf_v1 index on msg
            q += " AND hasToken(msg, {text:String})" if text.isalnum() else " AND positionCaseInsensitive(msg, {text:String}) > 0"
            p["text"] = text
        if service:
            q += " AND service = {service:String}"; p["service"] = service
        if level:
            q += " AND level = {level:String}"; p["level"] = level
        if start is not None:
            q += " AND ts >= {start:Float64}"; p["start"] = start
        if end is not None:
            q += " AND ts < {end:Float64}"; p["end"] = end
        if template_id:
            q += " AND template_id = {tid:String}"; p["tid"] = template_id
        return self._df(q + f" ORDER BY ts DESC LIMIT {int(limit)}", p)

    def purge_ttl(self, now=None):
        pass  # TTL clauses in the DDL expire rows automatically

    def close(self):
        self.client.close()


def make_store(cfg: dict) -> Store:
    s = cfg["storage"]
    if s["backend"] == "sqlite":
        return SQLiteStore(resolve_path(s["sqlite_path"]) if s["sqlite_path"] != ":memory:" else ":memory:",
                           s.get("retention_days"))
    if s["backend"] == "clickhouse":
        return ClickHouseStore(cfg)
    raise ValueError(f"unknown storage backend {s['backend']!r}")
