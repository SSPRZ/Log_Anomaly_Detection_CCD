"""Runtime wiring: each stage is a consumer-group loop over the bus.

In the full stack every stage runs as its own process/container against
Kafka (``python lap.py stage <name>``). In the lite stack they run
cooperatively in one process over the LocalBus.
"""
