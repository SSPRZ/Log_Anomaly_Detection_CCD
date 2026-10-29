"""S3 sink: hourly Parquet files of every topic (training, replay, audit).

Writes to ``archive.uri``: a local directory (lite) or ``s3://bucket/prefix``
(MinIO / S3 / GCS via pyarrow's filesystem layer). Layout:

    <uri>/<topic>/dt=YYYY-MM-DD/hour=HH/part-<n>.parquet
"""
from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from datetime import datetime, timezone

import pyarrow as pa
import pyarrow.parquet as pq

from common.config import resolve_path
from common.timeutil import parse_ts


class ParquetArchive:
    def __init__(self, uri: str = "data/archive", flush_rows: int = 50_000):
        self.flush_rows = flush_rows
        self.buffers: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
        self.parts: dict[tuple[str, str, str], int] = defaultdict(int)
        if uri.startswith("s3://"):
            from pyarrow import fs

            self.fs = fs.S3FileSystem(endpoint_override=os.environ.get("S3_ENDPOINT") or None,
                                      access_key=os.environ.get("AWS_ACCESS_KEY_ID"),
                                      secret_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
                                      scheme="http" if os.environ.get("S3_ENDPOINT", "").startswith("http:") else "https",
                                      region=os.environ.get("AWS_REGION", "us-east-1"))
            self.base = uri[len("s3://"):]
        else:
            self.fs = None
            self.base = str(resolve_path(uri))

    @staticmethod
    def _event_ts(topic: str, v: dict) -> float:
        for k in ("ts", "window_start", "start"):
            if v.get(k) is not None:
                try:
                    return parse_ts(v[k])  # raw records carry ISO strings, later topics epoch floats
                except (ValueError, TypeError):
                    continue
        return time.time()  # unparseable: archive by arrival time rather than 1970

    def add(self, topic: str, value: dict) -> None:
        dt = datetime.fromtimestamp(self._event_ts(topic, value), tz=timezone.utc)
        key = (topic, dt.strftime("%Y-%m-%d"), dt.strftime("%H"))
        self.buffers[key].append({"event_ts": self._event_ts(topic, value),
                                  "value": json.dumps(value, separators=(",", ":"), default=str)})
        if len(self.buffers[key]) >= self.flush_rows:
            self._flush(key)

    def _flush(self, key) -> None:
        rows = self.buffers.pop(key, [])
        if not rows:
            return
        topic, day, hour = key
        n = self.parts[key]
        self.parts[key] += 1
        path = f"{self.base}/{topic}/dt={day}/hour={hour}/part-{n:05d}.parquet"
        table = pa.Table.from_pylist(rows)
        if self.fs is None:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            pq.write_table(table, path, compression="zstd")
        else:
            pq.write_table(table, path, filesystem=self.fs, compression="zstd")

    def flush(self) -> None:
        for key in list(self.buffers):
            self._flush(key)
