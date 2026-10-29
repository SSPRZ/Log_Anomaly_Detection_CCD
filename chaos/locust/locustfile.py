"""Locust load profile with daily and weekly seasonality (Section 9.1).

    locust -f chaos/locust/locustfile.py --host http://localhost:8090 --headless

Drives POST /checkout on the simulated shop. The LoadShape compresses one day
into ``LAP_DAY_SECONDS`` (default 1 h) so a seasonal cycle fits in a demo;
set it to 86400 for real-time seasonality.
"""
from __future__ import annotations

import math
import os

from locust import HttpUser, LoadTestShape, between, task

DAY = float(os.environ.get("LAP_DAY_SECONDS", 3600))
PEAK_USERS = int(os.environ.get("LAP_PEAK_USERS", 50))


class Shopper(HttpUser):
    wait_time = between(0.5, 2.0)

    @task
    def checkout(self):
        self.client.post("/checkout", name="checkout")


class Seasonal(LoadTestShape):
    """Trough at 04:00, peak at 16:00, weekends at 70 %."""

    def tick(self):
        t = self.get_run_time()
        hour = (t % DAY) / DAY * 24
        weekday = int(t // DAY) % 7
        daily = 0.30 + 0.70 * 0.5 * (1 - math.cos(2 * math.pi * (hour - 4) / 24))
        users = max(1, int(PEAK_USERS * daily * (0.7 if weekday >= 5 else 1.0)))
        return users, max(1, users // 5)
