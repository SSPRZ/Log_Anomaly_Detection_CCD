# Evaluation report: eval-v5

History 7 d (1,218,061 records), evaluation 2 d (373,653 records), 12 injected incidents, model v0001.

| Metric | Platform | Static rule (>50 errors / 5 min) | Target |
|---|---|---|---|
| Precision | 0.88 | 0.00 | >= 0.80 |
| Recall | 1.00 | 0.00 | >= 0.70 |
| F1 | 0.93 | 0.00 | |
| Alerts | 24 (3 false) | 0 (0 false) | |
| Alerts / service / day | 2.40 | 0.00 | < 5 |
| Median detection delay (s, from fault start) | 188.3 | - | |
| e2e latency p95 (s, newest log in window -> score) | 40.7 | - | < 60 |

## Recall by fault kind

| Fault | Incidents | Platform detected | Median delay (s) | Static detected |
|---|---|---|---|---|
| bad_deploy | 2 | 2 | 177.7 | 0 |
| crash_loop | 2 | 2 | 192.5 | 0 |
| downstream_timeout | 2 | 2 | 160.9 | 0 |
| memory_leak | 2 | 2 | 1190.9 | 0 |
| silent | 2 | 2 | 135.3 | 0 |
| skip_step | 2 | 2 | 639.9 | 0 |

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
| recall_error_spike | 1.00 |
| recall_host_drop | 0.54 |
| recall_volume_spike | 0.94 |
| recall_latency | 0.80 |
| recall_new_templates | 1.00 |
| recall_mix_shift | 0.80 |
| recall_volume_drop | 0.86 |
| n_eval | 10886 |
| n_pos | 806 |
| train_seconds | 52.07 |
| gate_passed | True |

## Alerts

| Alert | Service | Severity | Window start | Score | Matched incident |
|---|---|---|---|---|---|
| al-20260915-0024-2f9a | gateway | P1 | 09-15T22:10 | 0.9 | flt-6186ac11 |
| al-20260915-0023-584e | orders | P1 | 09-15T18:01 | 0.9 | flt-0646915a |
| al-20260915-0022-4d59 | orders | P2 | 09-15T17:58 | 0.85 | flt-0646915a |
| al-20260915-0021-5221 | inventory | P1 | 09-15T17:55 | 0.967 | flt-0646915a |
| al-20260915-0020-88e3 | gateway | P2 | 09-15T17:54 | 0.85 | flt-0646915a |
| al-20260915-0019-d870 | orders | P2 | 09-15T13:03 | 0.85 | flt-3406e711 |
| al-20260915-0018-f802 | inventory | P2 | 09-15T13:01 | 0.85 | **false alarm** |
| al-20260915-0017-9505 | gateway | P2 | 09-15T13:00 | 0.85 | flt-3406e711 |
| al-20260915-0015-b06a | gateway | P2 | 09-15T08:20 | 0.85 | flt-29d868bc |
| al-20260915-0014-ea92 | payments | P1 | 09-15T08:20 | 1.0 | flt-29d868bc |
| al-20260915-0016-fb92 | orders | P2 | 09-15T08:20 | 0.85 | flt-29d868bc |
| al-20260915-0013-c2ca | gateway | P1 | 09-15T04:22 | 0.973 | flt-7c922941 |
| al-20260915-0011-9be2 | orders | P1 | 09-15T04:22 | 0.968 | flt-7c922941 |
| al-20260915-0012-495a | payments | P1 | 09-15T04:22 | 1.0 | flt-7c922941 |
| al-20260915-0010-c609 | gateway | P1 | 09-15T01:11 | 0.995 | flt-ef4ba042 |
| al-20260914-0009-6aed | notifications | P1 | 09-14T22:57 | 0.9 | flt-31c7faa6 |
| al-20260914-0008-583d | gateway | P2 | 09-14T17:12 | 0.85 | flt-65bb4a13 |
| al-20260914-0007-29c5 | payments | P2 | 09-14T12:22 | 0.85 | flt-ace1c403 |
| al-20260914-0006-65a9 | orders | P1 | 09-14T08:25 | 0.971 | flt-9f1fbc49 |
| al-20260914-0005-8838 | payments | P2 | 09-14T05:38 | 0.85 | flt-164de4de |
| al-20260914-0004-2bdf | gateway | P2 | 09-14T04:18 | 0.732 | **false alarm** |
| al-20260914-0003-c3b4 | inventory | P2 | 09-14T02:50 | 0.85 | **false alarm** |
| al-20260914-0002-8aff | gateway | P1 | 09-14T02:42 | 0.929 | flt-dcf5aa77 |
| al-20260914-0001-4982 | orders | P1 | 09-14T02:41 | 0.9 | flt-dcf5aa77 |

## Injected incidents

| Id | Kind | Service | Start | Minutes |
|---|---|---|---|---|
| flt-dcf5aa77 | bad_deploy | orders | 09-14T02:40 | 30 |
| flt-164de4de | skip_step | orders | 09-14T05:21 | 30 |
| flt-9f1fbc49 | crash_loop | orders | 09-14T08:22 | 15 |
| flt-ace1c403 | skip_step | orders | 09-14T12:19 | 30 |
| flt-65bb4a13 | memory_leak | gateway | 09-14T16:52 | 120 |
| flt-31c7faa6 | silent | notifications | 09-14T22:55 | 20 |
| flt-ef4ba042 | memory_leak | gateway | 09-15T00:53 | 120 |
| flt-7c922941 | downstream_timeout | payments | 09-15T04:20 | 20 |
| flt-29d868bc | downstream_timeout | payments | 09-15T08:18 | 20 |
| flt-3406e711 | bad_deploy | orders | 09-15T12:57 | 30 |
| flt-0646915a | crash_loop | inventory | 09-15T17:52 | 15 |
| flt-6186ac11 | silent | gateway | 09-15T22:09 | 20 |

_Wall time 218.3 s._
