"""Model registry with champion / challenger aliases.

A *bundle* (one version) contains every tier's artefacts:

    data/models/v0003/
        meta.json              weights, threshold, metrics, data window
        baseline.joblib        T1 samples + trend calibration
        iforest/<group>/       T2 per service group (ONNX + joblib + calibration)
        sequence/              T3 LSTM / Markov

``registry.json`` holds the aliases. The scorer polls its mtime and hot-reloads
when ``champion`` moves. When MLflow is enabled every version is also logged as
a run and registered under ``lap-anomaly-bundle`` with the same aliases, so the
MLflow UI is the system of record for experiments.
"""
from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

import joblib

from common.config import resolve_path
from detection.baseline import RobustBaseline
from detection.iforest import GroupForest
from detection.sequence import SequenceDetector


@dataclass
class Bundle:
    version: str
    baseline: RobustBaseline
    forests: dict[str, GroupForest]
    sequence: SequenceDetector | None
    weights: dict[str, float]
    threshold: float
    meta: dict = field(default_factory=dict)
    service_group: dict[str, str] = field(default_factory=dict)

    def forest_for(self, service: str) -> GroupForest | None:
        g = self.service_group.get(service)
        return self.forests.get(g) if g else None

    def save(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        joblib.dump(self.baseline.state(), directory / "baseline.joblib", compress=3)
        for g, f in self.forests.items():
            f.save(directory / "iforest" / g)
        if self.sequence is not None:
            self.sequence.save(directory / "sequence")
        meta = dict(self.meta, version=self.version, weights=self.weights, threshold=self.threshold,
                    service_group=self.service_group, groups=list(self.forests))
        (directory / "meta.json").write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")

    @classmethod
    def load(cls, directory: Path, cfg: dict) -> "Bundle":
        meta = json.loads((directory / "meta.json").read_text(encoding="utf-8"))
        baseline = RobustBaseline(cfg).load_state(joblib.load(directory / "baseline.joblib"))
        forests = {g: GroupForest.load(directory / "iforest" / g) for g in meta.get("groups", [])
                   if (directory / "iforest" / g).exists()}
        seq = SequenceDetector.load(directory / "sequence") if (directory / "sequence").exists() else None
        return cls(meta["version"], baseline, forests, seq, meta["weights"], meta["threshold"], meta,
                   meta.get("service_group", {}))


class Registry:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.root = resolve_path(cfg["detection"]["registry_dir"])
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / "registry.json"

    # ------------------------------------------------------------------ index
    def index(self) -> dict:
        if not self.index_path.exists():
            return {"versions": {}, "champion": None, "challenger": None, "history": []}
        return json.loads(self.index_path.read_text(encoding="utf-8"))

    def _write(self, idx: dict) -> None:
        tmp = self.index_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(idx, indent=2), encoding="utf-8")
        tmp.replace(self.index_path)

    def mtime(self) -> float:
        return self.index_path.stat().st_mtime if self.index_path.exists() else 0.0

    def next_version(self) -> str:
        nums = [int(v[1:]) for v in self.index()["versions"]] or [0]
        return "v%04d" % (max(nums) + 1)

    def path(self, version: str) -> Path:
        return self.root / version

    # ------------------------------------------------------------------ lifecycle
    def register(self, bundle: Bundle, metrics: dict, alias: str | None) -> None:
        bundle.save(self.path(bundle.version))
        idx = self.index()
        idx["versions"][bundle.version] = {"created": time.time(), "metrics": metrics, "status": alias or "archived"}
        if alias == "champion":
            self._set_champion(idx, bundle.version, reason="registered as champion")
        elif alias == "challenger":
            idx["challenger"] = bundle.version
            idx["versions"][bundle.version]["shadow_since"] = time.time()
        self._write(idx)
        self._mlflow(bundle, metrics, alias)

    def _set_champion(self, idx: dict, version: str, reason: str) -> None:
        prev = idx.get("champion")
        if prev and prev in idx["versions"]:
            idx["versions"][prev]["status"] = "archived"
        idx["champion"] = version
        idx["versions"][version]["status"] = "champion"
        if idx.get("challenger") == version:
            idx["challenger"] = None
        idx["history"].append({"ts": time.time(), "champion": version, "previous": prev, "reason": reason})

    def promote(self, version: str | None = None, reason: str = "manual promotion") -> str:
        idx = self.index()
        version = version or idx.get("challenger")
        if not version or version not in idx["versions"]:
            raise ValueError("nothing to promote")
        self._set_champion(idx, version, reason)
        self._write(idx)
        self._mlflow_alias(version, "champion")
        return version

    def rollback(self) -> str:
        """One-command rollback to the previous champion (Section 8)."""
        idx = self.index()
        for h in reversed(idx["history"]):
            if h["champion"] == idx["champion"] and h.get("previous"):
                prev = h["previous"]
                self._set_champion(idx, prev, reason=f"rollback from {idx['champion']}")
                self._write(idx)
                self._mlflow_alias(prev, "champion")
                return prev
        raise ValueError("no previous champion to roll back to")

    def load(self, alias: str = "champion") -> Bundle | None:
        v = self.index().get(alias)
        if not v or not self.path(v).exists():
            return None
        return Bundle.load(self.path(v), self.cfg)

    def prune(self, keep: int = 5) -> None:
        idx = self.index()
        protected = {idx.get("champion"), idx.get("challenger")}
        old = sorted(v for v in idx["versions"] if v not in protected)[:-keep or None]
        for v in old:
            shutil.rmtree(self.path(v), ignore_errors=True)
            idx["versions"].pop(v, None)
        self._write(idx)

    # ------------------------------------------------------------------ mlflow mirror
    def _mlflow_enabled(self) -> bool:
        return bool(self.cfg["detection"].get("mlflow", {}).get("enabled"))

    def _mlflow(self, bundle: Bundle, metrics: dict, alias: str | None) -> None:
        if not self._mlflow_enabled():
            return
        try:
            import logging
            import os
            import mlflow

            logging.getLogger("mlflow").setLevel(logging.ERROR)
            os.environ.setdefault("GIT_PYTHON_REFRESH", "quiet")
            m = self.cfg["detection"]["mlflow"]
            uri = m["tracking_uri"]
            if uri.startswith("file:./"):
                uri = resolve_path(uri[len("file:./"):]).as_uri()
            mlflow.set_tracking_uri(uri)
            mlflow.set_experiment(m["experiment"])
            with mlflow.start_run(run_name=bundle.version) as run:
                mlflow.log_params({"version": bundle.version, "threshold": bundle.threshold,
                                   **{f"w_{k}": v for k, v in bundle.weights.items()},
                                   "sequence_backend": bundle.sequence.backend if bundle.sequence else "none"})
                mlflow.log_metrics({k: float(v) for k, v in metrics.items() if isinstance(v, (int, float))})
                mlflow.log_artifacts(str(self.path(bundle.version)), artifact_path="bundle")
                client = mlflow.MlflowClient()
                name = "lap-anomaly-bundle"
                try:
                    client.create_registered_model(name)
                except Exception:
                    pass
                mv = client.create_model_version(name, f"{run.info.artifact_uri}/bundle", run.info.run_id,
                                                 tags={"lap_version": bundle.version})
                if alias:
                    client.set_registered_model_alias(name, alias, mv.version)
        except Exception as e:  # MLflow is a mirror; never block the lifecycle on it
            print(f"[registry] MLflow logging skipped: {type(e).__name__}: {e}")

    def _mlflow_alias(self, version: str, alias: str) -> None:
        if not self._mlflow_enabled():
            return
        try:
            import mlflow

            client = mlflow.MlflowClient()
            for mv in client.search_model_versions("name='lap-anomaly-bundle'"):
                if mv.tags.get("lap_version") == version:
                    client.set_registered_model_alias("lap-anomaly-bundle", alias, mv.version)
        except Exception:
            pass
