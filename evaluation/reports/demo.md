# Evaluation report: demo

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
| recall_volume_drop | 0.86 |
| recall_error_spike | 1.00 |
| recall_mix_shift | 0.80 |
| recall_volume_spike | 0.94 |
| recall_latency | 0.80 |
| recall_host_drop | 0.54 |
| n_eval | 10886 |
| n_pos | 806 |
| train_seconds | 117.13 |
| gate_passed | True |

## Alerts

| Alert | Service | Severity | Window start | Score | Matched incident |
|---|---|---|---|---|---|
| al-20260915-0023-27b8 | gateway | P1 | 09-15T22:10 | 0.9 | flt-719a97c1 |
| al-20260915-0022-d318 | orders | P1 | 09-15T18:01 | 0.9 | flt-fbbbf464 |
| al-20260915-0021-cbf6 | orders | P2 | 09-15T17:58 | 0.85 | flt-fbbbf464 |
| al-20260915-0020-610f | inventory | P1 | 09-15T17:55 | 0.967 | flt-fbbbf464 |
| al-20260915-0019-6c44 | gateway | P2 | 09-15T17:54 | 0.85 | flt-fbbbf464 |
| al-20260915-0018-224e | gateway | P2 | 09-15T13:04 | 0.85 | flt-f5724b63 |
| al-20260915-0017-2d06 | orders | P2 | 09-15T13:03 | 0.85 | flt-f5724b63 |
| al-20260915-0016-3b62 | inventory | P2 | 09-15T13:01 | 0.85 | **false alarm** |
| al-20260915-0014-2fac | gateway | P1 | 09-15T08:20 | 0.961 | flt-d0b0d06d |
| al-20260915-0013-f9ea | payments | P1 | 09-15T08:20 | 1.0 | flt-d0b0d06d |
| al-20260915-0015-1b28 | orders | P1 | 09-15T08:20 | 0.968 | flt-d0b0d06d |
| al-20260915-0010-a424 | payments | P1 | 09-15T04:22 | 1.0 | flt-2892e451 |
| al-20260915-0011-732c | gateway | P1 | 09-15T04:22 | 0.973 | flt-2892e451 |
| al-20260915-0012-dc06 | orders | P1 | 09-15T04:22 | 0.968 | flt-2892e451 |
| al-20260915-0009-8a17 | gateway | P1 | 09-15T01:11 | 0.995 | flt-9dd18a18 |
| al-20260914-0008-89e7 | notifications | P1 | 09-14T22:57 | 0.9 | flt-7ee52565 |
| al-20260914-0007-9ebd | gateway | P2 | 09-14T17:12 | 0.85 | flt-4ecdc99e |
| al-20260914-0006-b02e | payments | P2 | 09-14T12:22 | 0.85 | flt-18e6d0ee |
| al-20260914-0005-a4f2 | orders | P1 | 09-14T08:25 | 0.971 | flt-632f23cb |
| al-20260914-0004-1076 | payments | P2 | 09-14T05:38 | 0.85 | flt-8e6c1f22 |
| al-20260914-0003-968a | inventory | P2 | 09-14T02:50 | 0.85 | **false alarm** |
| al-20260914-0002-f64c | gateway | P1 | 09-14T02:42 | 0.955 | flt-aabdccc4 |
| al-20260914-0001-8977 | orders | P1 | 09-14T02:41 | 0.9 | flt-aabdccc4 |

## Injected incidents

| Id | Kind | Service | Start | Minutes |
|---|---|---|---|---|
| flt-aabdccc4 | bad_deploy | orders | 09-14T02:40 | 30 |
| flt-8e6c1f22 | skip_step | orders | 09-14T05:21 | 30 |
| flt-632f23cb | crash_loop | orders | 09-14T08:22 | 15 |
| flt-18e6d0ee | skip_step | orders | 09-14T12:19 | 30 |
| flt-4ecdc99e | memory_leak | gateway | 09-14T16:52 | 120 |
| flt-7ee52565 | silent | notifications | 09-14T22:55 | 20 |
| flt-9dd18a18 | memory_leak | gateway | 09-15T00:53 | 120 |
| flt-2892e451 | downstream_timeout | payments | 09-15T04:20 | 20 |
| flt-d0b0d06d | downstream_timeout | payments | 09-15T08:18 | 20 |
| flt-f5724b63 | bad_deploy | orders | 09-15T12:57 | 30 |
| flt-fbbbf464 | crash_loop | inventory | 09-15T17:52 | 15 |
| flt-719a97c1 | silent | gateway | 09-15T22:09 | 20 |

_Wall time 607.9 s._
