"""Drain online log-template mining (He et al., ICWS 2017).

A fixed-depth parse tree routes each message by token count and its first
tokens to a small group of clusters; the message joins the most similar
cluster (fraction of identical tokens >= sim_threshold), whose template is
generalised by replacing differing positions with <*>. Otherwise a new cluster
is created.

Template IDs are derived from the service and the template at creation time,
so they are stable across restarts; ``snapshot``/``restore`` persist the full
state to the log_templates table as the proposal requires.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

WILDCARD = "<*>"
_MASKS = [
    (re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"), WILDCARD),
    (re.compile(r"\b0x[0-9a-fA-F]+\b"), WILDCARD),
    (re.compile(r"\b[0-9a-fA-F]{12,}\b"), WILDCARD),
    (re.compile(r"(?<![A-Za-z])\d+(?:\.\d+)?"), WILDCARD),   # numbers, also inside tokens: ord-<*>
]
_SPLIT = re.compile(r"\s+")


def mask(msg: str) -> str:
    for pattern, repl in _MASKS:
        msg = pattern.sub(repl, msg)
    return msg


def _has_digit_or_wild(tok: str) -> bool:
    return WILDCARD in tok or any(c.isdigit() for c in tok)


@dataclass
class Cluster:
    id: str
    tokens: list[str]
    size: int = 0
    first_seen: float = 0.0
    last_seen: float = 0.0

    @property
    def template(self) -> str:
        return " ".join(self.tokens)


@dataclass
class _Node:
    children: dict = field(default_factory=dict)
    clusters: list = field(default_factory=list)  # cluster ids at leaf


class Drain:
    def __init__(self, namespace: str = "", depth: int = 4, sim_threshold: float = 0.5,
                 max_children: int = 100, max_clusters: int = 5000):
        if depth < 3:
            raise ValueError("depth must be >= 3")
        self.namespace = namespace
        self.depth = depth - 2  # levels of token prefixes below the length layer
        self.sim_threshold = sim_threshold
        self.max_children = max_children
        self.max_clusters = max_clusters
        self.root = _Node()
        self.clusters: dict[str, Cluster] = {}

    # ---------------------------------------------------------------- core
    def _make_id(self, tokens: list[str]) -> str:
        h = hashlib.sha1((self.namespace + "|" + " ".join(tokens)).encode()).hexdigest()[:12]
        return "t" + h

    def _leaf(self, tokens: list[str], create: bool) -> _Node | None:
        node = self.root.children.get(len(tokens))
        if node is None:
            if not create:
                return None
            node = self.root.children[len(tokens)] = _Node()
        for i in range(min(self.depth, len(tokens))):
            tok = tokens[i]
            key = WILDCARD if _has_digit_or_wild(tok) else tok
            nxt = node.children.get(key)
            if nxt is None and not create:
                nxt = node.children.get(WILDCARD)
            if nxt is None:
                if not create:
                    return None
                if len(node.children) >= self.max_children:
                    key = WILDCARD
                nxt = node.children.setdefault(key, _Node())
            node = nxt
        return node

    @staticmethod
    def _similarity(template: list[str], tokens: list[str]) -> tuple[float, int]:
        same = wild = 0
        for a, b in zip(template, tokens):
            if a == WILDCARD:
                wild += 1
            elif a == b:
                same += 1
        return same / len(tokens) if tokens else 1.0, wild

    def _best(self, leaf: _Node, tokens: list[str]) -> Cluster | None:
        best, best_sim, best_wild = None, -1.0, -1
        for cid in leaf.clusters:
            c = self.clusters[cid]
            sim, wild = self._similarity(c.tokens, tokens)
            if sim > best_sim or (sim == best_sim and wild > best_wild):
                best, best_sim, best_wild = c, sim, wild
        if best is not None and best_sim >= self.sim_threshold:
            return best
        return None

    def add(self, msg: str, ts: float = 0.0) -> tuple[Cluster, bool]:
        """Return (cluster, created)."""
        tokens = [t for t in _SPLIT.split(mask(msg).strip()) if t] or [""]
        leaf = self._leaf(tokens, create=True)
        cluster = self._best(leaf, tokens)
        created = False
        if cluster is None:
            cid = self._make_id(tokens)
            cluster = self.clusters.get(cid)
            if cluster is None:
                if len(self.clusters) >= self.max_clusters:
                    # degrade gracefully: fold into the leaf's largest cluster
                    if leaf.clusters:
                        cluster = max((self.clusters[c] for c in leaf.clusters), key=lambda c: c.size)
                    else:
                        cluster = Cluster(cid, tokens, first_seen=ts)
                        self.clusters[cid] = cluster
                        leaf.clusters.append(cid)
                else:
                    cluster = Cluster(cid, tokens, first_seen=ts)
                    self.clusters[cid] = cluster
                    leaf.clusters.append(cid)
                    created = True
        else:
            cluster.tokens = [a if a == b else WILDCARD for a, b in zip(cluster.tokens, tokens)]
        cluster.size += 1
        cluster.last_seen = max(cluster.last_seen, ts)
        return cluster, created

    def match(self, msg: str) -> Cluster | None:
        tokens = [t for t in _SPLIT.split(mask(msg).strip()) if t] or [""]
        leaf = self._leaf(tokens, create=False)
        return self._best(leaf, tokens) if leaf else None

    # ---------------------------------------------------------------- state
    def snapshot(self) -> list[dict]:
        return [{"template_id": c.id, "namespace": self.namespace, "pattern": c.template, "size": c.size,
                 "first_seen": c.first_seen, "last_seen": c.last_seen} for c in self.clusters.values()]

    def restore(self, rows: list[dict]) -> None:
        for r in rows:
            tokens = r["pattern"].split(" ")
            c = Cluster(r["template_id"], tokens, int(r.get("size", 0)), float(r.get("first_seen", 0)),
                        float(r.get("last_seen", 0)))
            self.clusters[c.id] = c
            leaf = self._leaf(tokens, create=True)
            if c.id not in leaf.clusters:
                leaf.clusters.append(c.id)
