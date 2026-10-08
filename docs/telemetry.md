# Telemetry and reduction analytics

## Collection

The ARQ worker runs `collect_metrics` every **30 seconds** (`CMP_TELEMETRY_INTERVAL_SECONDS`, must divide 60) and once at
start-up. For every appliance it reads, in parallel, the pool, the performance counters, the hardware inventory and the
current alarms, then stores them in one transaction:

| Reading | Stored in | Fields |
|---|---|---|
| Backup throughput | `throughput_samples` | `write_throughput_mb_s`, `read_throughput_mb_s`, `iops`, `active_streams` |
| Pool | `capacity_metrics` | raw usable GB, physical consumed GB, logical written GB, post-dedup GB, dedup / compression / total ratio |
| Hardware | `hardware_snapshots` | controller CPU and memory, NVRAM / dedup cache, power modules, disks, overall status |
| Alarms | `alarm_records` | Critical / Major / Warning; an alarm that disappears gets `cleared_at`; acknowledgement is kept |

* An appliance that cannot be reached is marked `fault` (with `last_error`); **no data is invented**. One failing appliance
  never stops the others.
* Target health: `fault` when hardware has failed or a Critical alarm is active; `degraded` when hardware is degraded or a
  Major alarm is active; otherwise `healthy` (Warning alarms alone do not change it).
* `purge_old_data` runs daily at 03:10: throughput and hardware samples are kept 30 days, capacity readings in full for
  14 days and then one per day for 400 days, cleared alarms 90 days.

## Formulas (`services/analytics/ratios.py`)

```
total reduction ratio = ingested / physical             25.0 means 25:1
deduplication ratio   = (ingested - post_dedup) / ingested   a 0-1 fraction (0.9 = 90 % removed)
compression ratio     = post_dedup / physical           N:1
```
`ingested * physical^-1 = (ingested / post_dedup) * (post_dedup / physical)`, so dedup factor x compression = total.
Several appliances are combined by summing the sizes first (sum of logical / sum of physical), never by averaging ratios.

## Runway (`services/analytics/runway.py`)

1. Keep the last physical-used reading of every UTC day.
2. Growth per day = difference to the previous day, divided by the number of days between them.
3. Simple moving average of the newest `CMP_RUNWAY_WINDOW_DAYS` (default 7) growth values.
4. `days_until_full = (raw - used) / average growth`.

| status | meaning |
|---|---|
| `ok` | `days_until_full` is a number |
| `insufficient_data` | fewer than two days of readings |
| `not_growing` | average growth is zero or negative (no number is shown) |
| `full` | no free space left (0 days) |

Limits: a plain average reacts slowly to a change of workload and is thrown off by uneven growth such as a weekly full backup;
a 7-day window smooths exactly one such cycle. The figure assumes the average continues; it is not a capacity plan.

## Endpoints (`/api/v1/oceanprotect`, header `X-API-Key`)

`target_id` narrows any call to one appliance; without it all appliances are combined.

| Call | Answer |
|---|---|
| `GET /overview` | `ingestion` (write / read / total MB/s, GB per hour, IOPS, streams), `reduction`, `runway` (the pool that fills first), `alarms` by severity, worst `hardware_status`, and a summary per appliance. Data older than 3 intervals marks the appliance `stale` and removes it from the ingestion figures |
| `GET /throughput/history?window=1h\|24h\|7d` | Averages and peaks in 1-minute, 15-minute or 1-hour buckets. Buckets without samples are left out. With several appliances the average is the sum of each appliance's average and the peak the sum of each one's own peak |
| `GET /reduction-stats?days=30` | Per day: logical written, physical used, post-dedup, the three ratios, and growth since the previous day. An appliance without a reading on a day keeps its last value |
| `GET /alarms?severity=&acknowledged=&state=active\|cleared\|all` | Alarms, most severe first, with counts for the whole filtered set |
| `POST /alarms/{id}/acknowledge` | Body `{"note": "..."}` optional. Records who and when, writes an audit entry. A second acknowledgement changes nothing. **Stored in the CMP only; nothing is sent to the appliance** |
