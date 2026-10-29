# Evaluation report: eval-v6

History 7 d (1,218,061 records), evaluation 2 d (373,653 records), 12 injected incidents, model v0001.

| Metric | Platform | Static >50 errors / 5 min (today) | Static >10 errors / 5 min | Target |
|---|---|---|---|---|
| Precision | 0.91 | 0.00 | 1.00 | >= 0.80 |
| Recall | 1.00 | 0.00 | 0.33 | >= 0.70 |
| F1 | 0.95 | 0.00 | 0.50 | |
| Alerts | 23 (2 false) | 0 (0 false) | 9 (0 false) | |
| Alerts / service / day | 2.30 | 0.00 | 0.90 | < 5 |
| Median detection delay (s, from fault start) | 182.8 | - | 283.5 | |
| e2e latency p95 (s, newest log in window -> score) | 37.6 | - | - | < 60 |

## Recall by fault kind

| Fault | Incidents | Platform detected | Median delay (s) | Static >50 detected | Static >10 detected |
|---|---|---|---|---|---|
| bad_deploy | 2 | 2 | 261.6 | 0 | 1 |
| crash_loop | 2 | 2 | 186.0 | 0 | 1 |
| downstream_timeout | 2 | 2 | 149.7 | 0 | 2 |
| memory_leak | 2 | 2 | 1184.7 | 0 | 0 |
| silent | 2 | 2 | 135.3 | 0 | 0 |
| skip_step | 2 | 2 | 639.9 | 0 | 0 |

## Training-time evaluation gate

| Metric | Value |
|---|---|
| precision | 0.85 |
| recall | 0.85 |
| f1 | 0.85 |
| tp | 684 |
| fp | 118 |
| fn | 122 |
| alerts_per_service_day | 1.43 |
| recall_new_templates | 1.00 |
| recall_error_spike | 1.00 |
| recall_volume_drop | 0.86 |
| recall_latency | 0.80 |
| recall_mix_shift | 0.80 |
| recall_host_drop | 0.54 |
| recall_volume_spike | 0.94 |
| n_eval | 10886 |
| n_pos | 806 |
| train_seconds | 351.61 |
| gate_passed | True |

## Alerts

| Alert | Service | Severity | Window start | Score | Matched incident |
|---|---|---|---|---|---|
| al-20260915-0023-799e | gateway | P1 | 09-15T22:10 | 0.9 | flt-41fc87ec |
| al-20260915-0022-12d4 | orders | P1 | 09-15T18:01 | 0.9 | flt-76f351c8 |
| al-20260915-0021-a749 | orders | P2 | 09-15T17:58 | 0.85 | flt-76f351c8 |
| al-20260915-0020-31dd | inventory | P1 | 09-15T17:55 | 0.967 | flt-76f351c8 |
| al-20260915-0019-f330 | gateway | P2 | 09-15T17:54 | 0.85 | flt-76f351c8 |
| al-20260915-0018-d705 | gateway | P2 | 09-15T13:04 | 0.85 | flt-a1e4e220 |
| al-20260915-0017-336f | orders | P2 | 09-15T13:03 | 0.85 | flt-a1e4e220 |
| al-20260915-0016-63df | inventory | P2 | 09-15T13:01 | 0.85 | **false alarm** |
| al-20260915-0014-4365 | gateway | P1 | 09-15T08:20 | 0.961 | flt-da2e40ac |
| al-20260915-0013-4225 | payments | P1 | 09-15T08:20 | 1.0 | flt-da2e40ac |
| al-20260915-0015-1d57 | orders | P1 | 09-15T08:20 | 0.968 | flt-da2e40ac |
| al-20260915-0010-9fce | payments | P1 | 09-15T04:22 | 1.0 | flt-484223cc |
| al-20260915-0011-0f28 | gateway | P1 | 09-15T04:22 | 0.973 | flt-484223cc |
| al-20260915-0012-0bba | orders | P1 | 09-15T04:22 | 0.968 | flt-484223cc |
| al-20260915-0009-4a8e | gateway | P1 | 09-15T01:11 | 0.995 | flt-2beebfed |
| al-20260914-0008-a4ec | notifications | P1 | 09-14T22:57 | 0.9 | flt-b6d5a35e |
| al-20260914-0007-7bff | gateway | P2 | 09-14T17:12 | 0.85 | flt-ae21d04f |
| al-20260914-0006-b274 | payments | P2 | 09-14T12:22 | 0.85 | flt-d99dea0f |
| al-20260914-0005-9d16 | orders | P1 | 09-14T08:25 | 0.971 | flt-b857d005 |
| al-20260914-0004-749c | payments | P2 | 09-14T05:38 | 0.85 | flt-b0529428 |
| al-20260914-0003-1037 | inventory | P2 | 09-14T02:50 | 0.85 | **false alarm** |
| al-20260914-0002-95a7 | gateway | P1 | 09-14T02:42 | 0.955 | flt-0da95bc3 |
| al-20260914-0001-7c69 | orders | P1 | 09-14T02:41 | 0.9 | flt-0da95bc3 |

## Injected incidents

| Id | Kind | Service | Start | Minutes |
|---|---|---|---|---|
| flt-0da95bc3 | bad_deploy | orders | 09-14T02:40 | 30 |
| flt-b0529428 | skip_step | orders | 09-14T05:21 | 30 |
| flt-b857d005 | crash_loop | orders | 09-14T08:22 | 15 |
| flt-d99dea0f | skip_step | orders | 09-14T12:19 | 30 |
| flt-ae21d04f | memory_leak | gateway | 09-14T16:52 | 120 |
| flt-b6d5a35e | silent | notifications | 09-14T22:55 | 20 |
| flt-2beebfed | memory_leak | gateway | 09-15T00:53 | 120 |
| flt-484223cc | downstream_timeout | payments | 09-15T04:20 | 20 |
| flt-da2e40ac | downstream_timeout | payments | 09-15T08:18 | 20 |
| flt-a1e4e220 | bad_deploy | orders | 09-15T12:57 | 30 |
| flt-76f351c8 | crash_loop | inventory | 09-15T17:52 | 15 |
| flt-41fc87ec | silent | gateway | 09-15T22:09 | 20 |

_Wall time 2105.4 s._
