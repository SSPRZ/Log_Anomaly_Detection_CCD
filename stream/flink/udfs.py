"""Python UDAFs used by window_features.sql."""
from __future__ import annotations

import math
import zlib

from pyflink.table import AggregateFunction, DataTypes
from pyflink.table.udf import udaf


class _Percentile(AggregateFunction):
    """Exact percentile over the window's values (windows are one service-minute, so small)."""

    def create_accumulator(self):
        return [[], 0.5]

    def accumulate(self, acc, value, q):
        if value is not None:
            acc[0].append(float(value))
        acc[1] = q

    def get_value(self, acc):
        vals = sorted(acc[0])
        if not vals:
            return None
        k = (len(vals) - 1) * acc[1]
        lo, hi = math.floor(k), math.ceil(k)
        return vals[lo] + (vals[hi] - vals[lo]) * (k - lo)


class _Entropy(AggregateFunction):
    def create_accumulator(self):
        return {}

    def accumulate(self, acc, tid):
        acc[tid] = acc.get(tid, 0) + 1

    def get_value(self, acc):
        n = sum(acc.values())
        return -sum(c / n * math.log2(c / n) for c in acc.values()) if n else 0.0


class _Hist(AggregateFunction):
    def create_accumulator(self):
        return [0] * 64

    def accumulate(self, acc, tid):
        acc[zlib.crc32(tid.encode()) % 64] += 1

    def get_value(self, acc):
        return acc


percentile_approx = udaf(_Percentile(), result_type=DataTypes.DOUBLE(),
                         accumulator_type=DataTypes.ARRAY(DataTypes.DOUBLE()), func_type="general")
template_entropy = udaf(_Entropy(), result_type=DataTypes.DOUBLE(), func_type="general")
template_hist = udaf(_Hist(), result_type=DataTypes.ARRAY(DataTypes.BIGINT()), func_type="general")
