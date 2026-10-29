"""Tier 3 -- DeepLog-style next-template model over per-trace sequences.

Each closed trace (from the Sessionizer) is a time-ordered list of
"service:template_id" tokens. Given the previous ``h`` tokens the model
predicts a distribution over the next one; a step whose actual token is not in
the top-k predictions (or is unknown) is a violation, attributed to the
service that logged it. This is what catches "workflow step skipped" faults
(e.g. an order confirmed without a payment authorisation).

Backends: an LSTM (PyTorch, as in DeepLog) when available, otherwise a
back-off n-gram (Markov) model with the same interface. Both are validated on
the public HDFS / BGL benchmarks via evaluation/benchmarks/loghub.py.
"""
from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

PAD, UNK = "<pad>", "<unk>"


def trace_tokens(trace: dict) -> list[tuple[str, str, float]]:
    return [(f"{e['service']}:{e['template_id']}", e["service"], e["ts"]) for e in trace["events"]]


class _Vocab:
    def __init__(self, tokens: list[str] | None = None):
        self.itos = [PAD, UNK] + sorted(set(tokens or []))
        self.stoi = {t: i for i, t in enumerate(self.itos)}

    def __len__(self):
        return len(self.itos)

    def idx(self, tok: str) -> int:
        return self.stoi.get(tok, 1)


class MarkovModel:
    backend = "markov"

    def __init__(self, h: int = 4, top_k: int = 3):
        self.h, self.top_k = h, top_k
        self.counts: dict[tuple, Counter] = defaultdict(Counter)

    def fit(self, sequences: list[list[str]], **_):
        for seq in sequences:
            padded = [PAD] * self.h + seq
            for i in range(self.h, len(padded)):
                for n in range(self.h, -1, -1):
                    self.counts[tuple(padded[i - n:i])][padded[i]] += 1
        return self

    def topk(self, history: list[str]) -> list[str]:
        hist = ([PAD] * self.h + history)[-self.h:]
        for n in range(self.h, -1, -1):
            c = self.counts.get(tuple(hist[len(hist) - n:]) if n else ())
            if c:
                return [t for t, _ in c.most_common(self.top_k)]
        return []

    def state(self) -> dict:
        return {"h": self.h, "top_k": self.top_k,
                "counts": [[list(k), dict(v)] for k, v in self.counts.items()]}

    @classmethod
    def from_state(cls, st: dict) -> "MarkovModel":
        m = cls(st["h"], st["top_k"])
        for k, v in st["counts"]:
            m.counts[tuple(k)] = Counter(v)
        return m


class LSTMModel:
    backend = "torch"

    def __init__(self, h: int = 4, top_k: int = 3, hidden: int = 64, layers: int = 2, emb: int = 32):
        self.h, self.top_k, self.hidden, self.layers, self.emb = h, top_k, hidden, layers, emb
        self.vocab = _Vocab()
        self.net = None

    def _build(self, n_tokens: int):
        import torch.nn as nn

        class Net(nn.Module):
            def __init__(s):
                super().__init__()
                s.emb = nn.Embedding(n_tokens, self.emb, padding_idx=0)
                s.lstm = nn.LSTM(self.emb, self.hidden, self.layers, batch_first=True)
                s.out = nn.Linear(self.hidden, n_tokens)

            def forward(s, x):
                y, _ = s.lstm(s.emb(x))
                return s.out(y[:, -1, :])

        return Net()

    def _windows(self, sequences: list[list[str]]):
        X, Y = [], []
        for seq in sequences:
            ids = [0] * self.h + [self.vocab.idx(t) for t in seq]
            for i in range(self.h, len(ids)):
                X.append(ids[i - self.h:i])
                Y.append(ids[i])
        return np.asarray(X, dtype=np.int64), np.asarray(Y, dtype=np.int64)

    def fit(self, sequences: list[list[str]], epochs: int = 6, batch: int = 512, lr: float = 3e-3, seed: int = 0):
        import torch

        torch.manual_seed(seed)
        self.vocab = _Vocab([t for s in sequences for t in s])
        self.net = self._build(len(self.vocab))
        X, Y = self._windows(sequences)
        if len(X) == 0:
            return self
        # de-duplicate (history, next) pairs with counts -> far fewer steps on repetitive workflows
        pairs = Counter(zip(map(tuple, X), Y))
        Xu = torch.tensor([list(k[0]) for k in pairs], dtype=torch.long)
        Yu = torch.tensor([k[1] for k in pairs], dtype=torch.long)
        W = torch.tensor([math.log1p(c) + 1.0 for c in pairs.values()], dtype=torch.float32)
        opt = torch.optim.Adam(self.net.parameters(), lr=lr)
        loss_fn = torch.nn.CrossEntropyLoss(reduction="none")
        n = len(Xu)
        self.net.train()
        batches_per_epoch = max(1, math.ceil(n / batch))
        for _ in range(max(epochs, math.ceil(400 / batches_per_epoch))):  # >= ~400 optimiser steps
            perm = torch.randperm(n)
            for i in range(0, n, batch):
                idx = perm[i:i + batch]
                opt.zero_grad()
                loss = (loss_fn(self.net(Xu[idx]), Yu[idx]) * W[idx]).mean()
                loss.backward()
                opt.step()
        self.net.eval()
        return self

    def topk_batch(self, histories: list[list[str]]) -> list[list[str]]:
        import torch

        if self.net is None or not histories:
            return [[] for _ in histories]
        if not hasattr(self, "_cache"):
            self._cache: dict[tuple, list[str]] = {}
        keys = [tuple([0] * max(0, self.h - len(hh)) + [self.vocab.idx(t) for t in hh[-self.h:]])
                for hh in histories]
        missing = list({k for k in keys if k not in self._cache})
        if missing:
            with torch.no_grad():
                logits = self.net(torch.tensor(missing, dtype=torch.long))
                logits[:, 0] = -1e9  # never predict padding
                top = torch.topk(logits, min(self.top_k, logits.shape[1]), dim=1).indices.tolist()
            if len(self._cache) > 200_000:
                self._cache.clear()
            for k, row in zip(missing, top):
                self._cache[k] = [self.vocab.itos[i] for i in row]
        return [self._cache[k] for k in keys]

    def topk(self, history: list[str]) -> list[str]:
        return self.topk_batch([history])[0]

    def save(self, directory: Path) -> None:
        import torch

        torch.save(self.net.state_dict(), directory / "sequence_lstm.pt")
        (directory / "sequence_vocab.json").write_text(json.dumps(
            {"itos": self.vocab.itos, "h": self.h, "top_k": self.top_k, "hidden": self.hidden,
             "layers": self.layers, "emb": self.emb}), encoding="utf-8")

    @classmethod
    def load(cls, directory: Path) -> "LSTMModel":
        import torch

        meta = json.loads((directory / "sequence_vocab.json").read_text(encoding="utf-8"))
        m = cls(meta["h"], meta["top_k"], meta["hidden"], meta["layers"], meta["emb"])
        m.vocab = _Vocab()
        m.vocab.itos = meta["itos"]
        m.vocab.stoi = {t: i for i, t in enumerate(m.vocab.itos)}
        m.net = m._build(len(m.vocab.itos))
        m.net.load_state_dict(torch.load(directory / "sequence_lstm.pt", weights_only=True))
        m.net.eval()
        return m


class SequenceDetector:
    """Wraps a backend; scores traces and keeps per-service violation baselines."""

    def __init__(self, h: int = 4, top_k: int = 3, backend: str = "auto", epochs: int = 6):
        self.h, self.top_k, self.epochs = h, top_k, epochs
        if backend == "auto":
            try:
                import torch  # noqa: F401
                backend = "torch"
            except (ImportError, OSError):
                backend = "markov"
        self.backend = backend
        self.model = LSTMModel(h, top_k) if backend == "torch" else MarkovModel(h, top_k)
        self.base_rate: dict[str, float] = {}   # service -> violation rate per trace touching it

    def fit(self, traces: list[dict]) -> "SequenceDetector":
        seqs = [[t for t, _, _ in trace_tokens(tr)] for tr in traces]
        seqs = [s for s in seqs if len(s) >= 2]
        self.model.fit(seqs, epochs=self.epochs)
        # calibrate the normal violation rate per service on the training traces themselves
        touched, viol = Counter(), Counter()
        for tr in traces[-20000:]:
            blamed = self.attribute(tr)
            for svc in {s for _, s, _ in trace_tokens(tr)} | ({blamed["service"]} if blamed else set()):
                touched[svc] += 1
            if blamed:
                viol[blamed["service"]] += 1
        self.base_rate = {s: (viol[s] + 1) / (touched[s] + 50) for s in touched}
        return self

    def attribute(self, trace: dict) -> dict | None:
        """One verdict per trace: the *first* violation, blamed on the service whose step was expected.

        After the first deviation every later step looks wrong too (the model has
        never seen that context), so only the first one is informative. Blaming
        the expected step's service handles missing steps correctly: a skipped
        payment is the payment step's problem, and a gateway that stopped logging
        shows up as traces whose expected first gateway event is absent.
        """
        viol = self.check(trace)
        if not viol:
            return None
        v = viol[0]
        expected = v.get("expected") or []
        svc = expected[0].split(":", 1)[0] if expected and expected[0] not in (PAD, UNK) else v["service"]
        return {**v, "service": svc, "observed_service": v["service"]}

    def check(self, trace: dict) -> list[dict]:
        """Return violations: [{service, ts, token, expected}]."""
        toks = trace_tokens(trace)
        if not toks:
            return []
        # include the first step (empty history): a trace that does not start where workflows
        # normally start is itself a violation
        histories = [[t for t, _, _ in toks[:i]] for i in range(0, len(toks))]
        if isinstance(self.model, LSTMModel):
            preds = self.model.topk_batch(histories)
        else:
            preds = [self.model.topk(hh) for hh in histories]
        out = []
        for (tok, svc, ts), pred in zip(toks, preds):
            if tok not in pred:
                out.append({"service": svc, "ts": ts, "token": tok, "expected": pred[:3]})
        return out

    @staticmethod
    def window_score(n_traces: int, n_violating: int, base_rate: float) -> float:
        """Binomial z of violating traces vs the service's normal rate, mapped to 0..1."""
        if n_traces <= 0:
            return 0.0
        p = min(max(base_rate, 1e-3), 0.5)
        z = (n_violating - n_traces * p) / math.sqrt(n_traces * p * (1 - p) + 1e-9)
        frac = n_violating / n_traces
        if frac < 0.05 or n_violating < 2:  # need a material share of broken workflows
            return 0.0
        return float(1.0 / (1.0 + math.exp(-(z - 4.0) / 1.0)))

    def save(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        meta = {"backend": self.backend, "h": self.h, "top_k": self.top_k, "base_rate": self.base_rate}
        if self.backend == "torch":
            self.model.save(directory)
        else:
            (directory / "sequence_markov.json").write_text(json.dumps(self.model.state()), encoding="utf-8")
        (directory / "sequence_meta.json").write_text(json.dumps(meta), encoding="utf-8")

    @classmethod
    def load(cls, directory: Path) -> "SequenceDetector | None":
        p = directory / "sequence_meta.json"
        if not p.exists():
            return None
        meta = json.loads(p.read_text(encoding="utf-8"))
        d = cls.__new__(cls)
        d.h, d.top_k, d.epochs, d.backend = meta["h"], meta["top_k"], 0, meta["backend"]
        d.base_rate = meta["base_rate"]
        if d.backend == "torch":
            d.model = LSTMModel.load(directory)
        else:
            d.model = MarkovModel.from_state(json.loads((directory / "sequence_markov.json").read_text("utf-8")))
        return d
