# Onboarding a service

1. **Log structured JSON to stdout** with the shared library (`services/loglib`). Required: `ts`, `service`, `env`,
   `level`, `msg`. Recommended: `trace_id` (enables the sequence model), `attrs.latency_ms`, `attrs.status`.
   Legacy plain text is accepted if it matches a profile in `stream/parse_enrich.py:PROFILES`.
2. **Never log secrets on purpose.** Redaction (`stream/redaction.py`) is a safety net, not a licence: e-mails,
   card-like numbers, IPs, bearer/JWT/API tokens and `password=`-style pairs are masked before storage.
3. **Label the Deployment** with `app.kubernetes.io/name: <service>`; Fluent Bit uses it when `service` is missing.
4. **Add the service to `config/topology.yaml`** with its tier (drives P1 eligibility), team Slack channel and the
   services it calls (drives incident grouping).
5. **Emit deploy events** from CI to `deploys.events` (`{"ts", "service", "version", "commit"}`) so baselines
   re-warm after releases instead of alerting on the new normal.
6. **Wait for warm-up.** Hard rules start after 6 h of history; T1 needs ~20 samples per hour-of-week slot (falls back
   to hour-of-day / service-wide until then); T2/T3 join after the next nightly training.
7. Verify on the dashboard (Templates page shows the mined templates; Overview shows the score series).
