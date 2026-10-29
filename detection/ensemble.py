"""Ensemble of tier scores + 2-of-3 debounce."""
from __future__ import annotations

from collections import defaultdict, deque


class Ensemble:
    def __init__(self, weights: dict[str, float], threshold: float):
        self.weights = dict(weights)
        self.threshold = float(threshold)

    def combine(self, tiers: dict[str, float | None]) -> float:
        """Weighted mean over the tiers that produced a score (missing tiers re-weight the rest).

        A single tier that is extremely confident still pushes the result up:
        the combined score is never below 0.85 x the strongest tier, so one
        clear signal is not averaged away by tiers that cannot see it.
        """
        num = den = 0.0
        best = 0.0
        for name, w in self.weights.items():
            s = tiers.get(name)
            if s is None:
                continue
            num += w * s
            den += w
            best = max(best, s)
        if den == 0:
            return 0.0
        return float(max(num / den, 0.85 * best if best >= 0.9 else 0.0))


class Debouncer:
    """Fire when >= hits of the last n windows of a service exceed the threshold."""

    def __init__(self, n: int = 3, hits: int = 2):
        self.n, self.hits = n, hits
        self.history: dict[str, deque] = defaultdict(lambda: deque(maxlen=self.n))

    def update(self, service: str, over: bool) -> bool:
        h = self.history[service]
        h.append(bool(over))
        return over and sum(h) >= self.hits
