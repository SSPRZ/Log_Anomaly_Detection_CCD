# Developer entry points (Section 5.1: "make up in under 5 minutes").
PY ?= python
COMPOSE = docker compose -f deploy/compose/docker-compose.yml

.PHONY: help install up down logs train demo live dashboard test test-all lint evaluate inject

help:
	@echo "install     create deps (pip)            demo       lite end-to-end demo + report"
	@echo "up / down   full Docker Compose stack    live       lite stack in real time"
	@echo "train       train + register (compose)   dashboard  Streamlit UI"
	@echo "test        unit tests                   test-all   unit + end-to-end"
	@echo "evaluate    M5 evaluation report         inject     inject a fault into the running stack"

install:
	$(PY) -m pip install -r requirements-dev.txt

# ---------------------------------------------------------------- full stack
up:
	$(COMPOSE) up -d --build
	@echo "Grafana http://localhost:3000  Dashboard http://localhost:8501  MLflow http://localhost:5000"
	@echo "Fault injector http://localhost:8090/docs  Prometheus http://localhost:9090"

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=50 parse-enrich window-features anomaly-scorer alert-router

train:
	$(COMPOSE) --profile jobs run --rm trainer

inject:  # make inject KIND=downstream_timeout SERVICE=payments MIN=10
	curl -s -X POST localhost:8090/faults -H 'content-type: application/json' \
	  -d '{"kind":"$(or $(KIND),downstream_timeout)","service":"$(or $(SERVICE),payments)","duration_min":$(or $(MIN),10)}'

# ---------------------------------------------------------------- lite stack (no Docker)
demo:
	$(PY) lap.py demo

live:
	$(PY) lap.py live

dashboard:
	$(PY) lap.py dashboard

evaluate:
	$(PY) lap.py evaluate --history-days 7 --eval-days 2

# ---------------------------------------------------------------- quality
lint:
	ruff check .

test:
	$(PY) -m pytest --deselect tests/test_end_to_end.py

test-all:
	$(PY) -m pytest
