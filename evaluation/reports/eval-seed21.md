# Evaluation report: eval-seed21

History 7 d (1,217,667 records), evaluation 3 d (560,074 records), 18 injected incidents, model v0001.

| Metric | Platform | Static >50 errors / 5 min (today) | Static >10 errors / 5 min | Target |
|---|---|---|---|---|
| Precision | 0.89 | 0.00 | 1.00 | >= 0.80 |
| Recall | 1.00 | 0.00 | 0.44 | >= 0.70 |
| F1 | 0.94 | 0.00 | 0.62 | |
| Alerts | 36 (4 false) | 0 (0 false) | 17 (0 false) | |
| Alerts / service / day | 2.40 | 0.00 | 1.13 | < 5 |
| Median detection delay (s, from fault start) | 154.7 | - | 396.9 | |
| e2e latency p95 (s, newest log in window -> score) | 40.9 | - | - | < 60 |

## Recall by fault kind

| Fault | Incidents | Platform detected | Median delay (s) | Static >50 detected | Static >10 detected |
|---|---|---|---|---|---|
| bad_deploy | 3 | 3 | 187.6 | 0 | 3 |
| crash_loop | 3 | 3 | 225.8 | 0 | 1 |
| downstream_timeout | 3 | 3 | 130.6 | 0 | 3 |
| memory_leak | 3 | 3 | 802.2 | 0 | 1 |
| silent | 3 | 3 | 119.2 | 0 | 0 |
| skip_step | 3 | 3 | 147.6 | 0 | 0 |

## Training-time evaluation gate

| Metric | Value |
|---|---|
| precision | 0.84 |
| recall | 0.84 |
| f1 | 0.84 |
| tp | 680 |
| fp | 129 |
| fn | 126 |
| alerts_per_service_day | 1.00 |
| recall_volume_drop | 0.85 |
| recall_host_drop | 0.52 |
| recall_volume_spike | 0.96 |
| recall_latency | 0.73 |
| recall_new_templates | 1.00 |
| recall_error_spike | 1.00 |
| recall_mix_shift | 0.84 |
| n_eval | 10886 |
| n_pos | 806 |
| train_seconds | 63.69 |
| gate_passed | True |

## Alerts

| Alert | Service | Severity | Window start | Score | Matched incident |
|---|---|---|---|---|---|
| al-20260916-0036-67ea | inventory | P1 | 09-16T22:29 | 0.965 | flt-9ababb8e |
| al-20260916-0034-4a96 | gateway | P1 | 09-16T22:27 | 0.916 | flt-9ababb8e |
| al-20260916-0035-35df | orders | P1 | 09-16T22:27 | 0.922 | flt-9ababb8e |
| al-20260916-0033-2610 | gateway | P2 | 09-16T17:30 | 0.85 | flt-b90ad09d |
| al-20260916-0032-409d | payments | P2 | 09-16T14:51 | 0.85 | flt-164dda58 |
| al-20260916-0030-1893 | gateway | P1 | 09-16T08:24 | 0.908 | flt-4e41d731 |
| al-20260916-0031-f5ee | inventory | P1 | 09-16T08:24 | 0.96 | flt-4e41d731 |
| al-20260916-0029-609b | orders | P1 | 09-16T05:42 | 0.9 | flt-94833941 |
| al-20260916-0028-1ed2 | orders | P1 | 09-16T05:38 | 0.912 | flt-94833941 |
| al-20260916-0026-2d66 | gateway | P1 | 09-16T05:37 | 0.924 | flt-94833941 |
| al-20260916-0027-6dea | inventory | P1 | 09-16T05:37 | 0.974 | flt-94833941 |
| al-20260916-0025-4eeb | notifications | P3 | 09-16T05:30 | 0.655 | **false alarm** |
| al-20260916-0024-1f75 | notifications | P1 | 09-16T00:37 | 0.9 | flt-6efb080d |
| al-20260915-0023-d11d | orders | P2 | 09-15T21:57 | 0.846 | flt-3bc15661 |
| al-20260915-0022-c727 | gateway | P2 | 09-15T16:55 | 0.85 | flt-9c43ae82 |
| al-20260915-0021-3ef9 | notifications | P1 | 09-15T12:33 | 0.9 | flt-37cf6bb5 |
| al-20260915-0020-a995 | gateway | P2 | 09-15T10:55 | 0.85 | flt-2d47d27b |
| al-20260915-0018-429b | orders | P2 | 09-15T10:55 | 0.85 | flt-2d47d27b |
| al-20260915-0019-0f05 | payments | P1 | 09-15T10:55 | 0.994 | flt-2d47d27b |
| al-20260915-0017-1389 | inventory | P2 | 09-15T06:11 | 0.85 | **false alarm** |
| al-20260915-0016-8cc1 | orders | P1 | 09-15T06:11 | 0.965 | flt-04aab047 |
| al-20260915-0015-49b7 | gateway | P1 | 09-15T06:10 | 0.935 | flt-04aab047 |
| al-20260915-0014-6d93 | notifications | P1 | 09-15T02:58 | 0.9 | flt-c93dc5c0 |
| al-20260914-0013-c4c4 | gateway | P1 | 09-14T21:22 | 0.946 | flt-6b3a5534 |
| al-20260914-0011-94de | orders | P1 | 09-14T21:22 | 0.964 | flt-6b3a5534 |
| al-20260914-0012-3c73 | payments | P1 | 09-14T21:22 | 0.994 | flt-6b3a5534 |
| al-20260914-0010-9ef5 | payments | P2 | 09-14T18:18 | 0.85 | flt-3e2f9af2 |
| al-20260914-0008-d244 | inventory | P2 | 09-14T13:24 | 0.85 | **false alarm** |
| al-20260914-0009-bcb7 | gateway | P2 | 09-14T13:24 | 0.85 | flt-1ba48865 |
| al-20260914-0007-eea5 | orders | P1 | 09-14T13:22 | 0.9 | flt-1ba48865 |
| al-20260914-0006-ea54 | gateway | P1 | 09-14T09:14 | 0.962 | flt-5c363491 |
| al-20260914-0005-3441 | gateway | P1 | 09-14T06:30 | 0.974 | flt-74e01cbc |
| al-20260914-0003-24d4 | payments | P1 | 09-14T06:29 | 0.994 | flt-74e01cbc |
| al-20260914-0004-4010 | orders | P1 | 09-14T06:29 | 0.96 | flt-74e01cbc |
| al-20260914-0002-77d1 | notifications | P3 | 09-14T03:57 | 0.719 | **false alarm** |
| al-20260914-0001-2e0c | payments | P2 | 09-14T00:27 | 0.85 | flt-56cdcadd |

## Injected incidents

| Id | Kind | Service | Start | Minutes |
|---|---|---|---|---|
| flt-56cdcadd | skip_step | orders | 09-14T00:21 | 30 |
| flt-74e01cbc | downstream_timeout | payments | 09-14T06:28 | 20 |
| flt-5c363491 | memory_leak | gateway | 09-14T09:02 | 120 |
| flt-1ba48865 | bad_deploy | orders | 09-14T13:22 | 30 |
| flt-3e2f9af2 | skip_step | orders | 09-14T18:17 | 30 |
| flt-6b3a5534 | downstream_timeout | payments | 09-14T21:21 | 20 |
| flt-c93dc5c0 | silent | notifications | 09-15T02:57 | 20 |
| flt-04aab047 | bad_deploy | orders | 09-15T06:04 | 30 |
| flt-2d47d27b | downstream_timeout | payments | 09-15T10:54 | 20 |
| flt-37cf6bb5 | silent | notifications | 09-15T12:31 | 20 |
| flt-9c43ae82 | memory_leak | gateway | 09-15T16:43 | 120 |
| flt-3bc15661 | crash_loop | orders | 09-15T21:49 | 15 |
| flt-6efb080d | silent | notifications | 09-16T00:36 | 20 |
| flt-94833941 | crash_loop | inventory | 09-16T05:34 | 15 |
| flt-4e41d731 | bad_deploy | inventory | 09-16T08:22 | 30 |
| flt-164dda58 | skip_step | orders | 09-16T14:49 | 30 |
| flt-b90ad09d | memory_leak | gateway | 09-16T17:17 | 120 |
| flt-9ababb8e | crash_loop | inventory | 09-16T22:25 | 15 |

_Wall time 10971.9 s._
