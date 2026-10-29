"""Validate the T3 sequence model on public Loghub benchmarks (Section 9.2).

HDFS (block-session labels) in the widely used DeepLog split format: one session
per line, space-separated event ids. Get the files from the DeepLog/loglizer
preprocessed HDFS release (``hdfs_train``, ``hdfs_test_normal``,
``hdfs_test_abnormal``) or produce them from Loghub's HDFS_v1 with
``--from-loghub HDFS.log anomaly_label.csv`` (structured with our Drain parser).

    python evaluation/benchmarks/loghub.py --train data/hdfs/hdfs_train \
        --normal data/hdfs/hdfs_test_normal --abnormal data/hdfs/hdfs_test_abnormal --backend torch

BGL (per-line labels) is evaluated with sliding windows: ``--bgl BGL.log --window 20``.
Published DeepLog results on HDFS are roughly P=0.95 / R=0.96 / F1=0.96 for comparison.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import common.preload  # noqa: E402,F401
from detection.sequence import SequenceDetector  # noqa: E402
from stream.drain import Drain  # noqa: E402


def read_sessions(path: str) -> list[list[str]]:
    with open(path, encoding="utf-8") as fh:
        return [ln.split() for ln in fh if ln.strip()]


def as_trace(seq: list[str], i: int) -> dict:
    return {"trace_id": str(i), "start": 0.0,
            "events": [{"ts": float(k), "service": "hdfs", "template_id": e} for k, e in enumerate(seq)]}


def from_loghub_hdfs(log_path: str, label_path: str) -> tuple[list[list[str]], list[list[str]], list[list[str]]]:
    labels = {}
    with open(label_path, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            labels[row["BlockId"]] = row["Label"] == "Anomaly"
    drain = Drain("hdfs")
    sessions: dict[str, list[str]] = defaultdict(list)
    blk = re.compile(r"(blk_-?\d+)")
    with open(log_path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            parts = line.split(maxsplit=5)
            if len(parts) < 6:
                continue
            c, _ = drain.add(parts[5])
            for b in set(blk.findall(line)):
                sessions[b].append(c.id)
    normal = [s for b, s in sessions.items() if not labels.get(b)]
    abnormal = [s for b, s in sessions.items() if labels.get(b)]
    n_train = max(1, int(len(normal) * 0.01))  # DeepLog trains on ~1 % of normal sessions
    return normal[:n_train], normal[n_train:], abnormal


def evaluate(train, normal, abnormal, backend: str, h: int, k: int, epochs: int) -> dict:
    det = SequenceDetector(h=h, top_k=k, backend=backend, epochs=epochs)
    det.fit([as_trace(s, i) for i, s in enumerate(train)])
    fp = sum(1 for i, s in enumerate(normal) if det.check(as_trace(s, i)))
    tp = sum(1 for i, s in enumerate(abnormal) if det.check(as_trace(s, i)))
    fn = len(abnormal) - tp
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return {"backend": det.backend, "precision": p, "recall": r, "f1": 2 * p * r / (p + r) if p + r else 0.0,
            "train": len(train), "test_normal": len(normal), "test_abnormal": len(abnormal)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--train"); ap.add_argument("--normal"); ap.add_argument("--abnormal")
    ap.add_argument("--from-loghub", nargs=2, metavar=("HDFS_LOG", "LABELS_CSV"))
    ap.add_argument("--backend", default="auto"); ap.add_argument("--h", type=int, default=10)
    ap.add_argument("--k", type=int, default=9); ap.add_argument("--epochs", type=int, default=10)
    a = ap.parse_args(argv)
    if a.from_loghub:
        train, normal, abnormal = from_loghub_hdfs(*a.from_loghub)
    elif a.train:
        train, normal, abnormal = read_sessions(a.train), read_sessions(a.normal), read_sessions(a.abnormal)
    else:
        ap.error("give --train/--normal/--abnormal or --from-loghub")
    print(evaluate(train, normal, abnormal, a.backend, a.h, a.k, a.epochs))


if __name__ == "__main__":
    main()
