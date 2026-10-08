# Backup orchestration

Start backups on an appliance, follow them until they end, cancel them, and see how they went.

## Objects

| Object | Where it lives | Notes |
|---|---|---|
| Policy (`/backup-policies`) | CMP only | cron schedule, full or incremental, retention days, WORM switch and mode |
| Asset (`/assets`) | CMP **and** appliance | registering one also creates it on the appliance (`array_asset_id`); deleting removes both |
| Job (`/backup-jobs`) | CMP, mirrors an appliance task | keeps its history when the asset or policy is deleted |

A policy is **not** pushed to the appliance yet. It decides the backup type and schedule of the backups the CMP starts, and
it is the place where retention and WORM are kept. Applying retention/WORM on the appliance comes later.

## Starting a backup

`POST /api/v1/backup-jobs` with `{"asset_id": "...", "policy_id": "...", "backup_type": "full|incremental"}`.
Only `asset_id` is required. Backup type: the request, else the policy's, else `full`. Policy: the request, else the asset's.

The answer is **202**: the backup runs on the appliance and the job starts as `PENDING`. Rules (all answer `409`):

* the policy is disabled;
* the asset already has a `PENDING` or `RUNNING` job (one backup per asset at a time);
* the asset is not registered on the appliance.

If the appliance cannot be reached the answer is `502` and no job is created. If the job cannot be stored after the appliance
accepted it, the task is cancelled on the appliance again, so no backup runs without a record.

## Following a job

```
PENDING -> RUNNING -> SUCCESS | FAILED | PARTIALLY_SUCCESSFUL | CANCELLED
```

* The worker checks every active job every `CMP_JOB_POLL_INTERVAL_SECONDS` (15; must divide 60) and stores status, start and end
  time, GB transferred, MB/s and the appliance's log lines.
* `GET /backup-jobs/{id}` returns the stored state. Add `?refresh=true` to ask the appliance right now; the answer then also has
  `progress_percent`. An unreachable appliance gives `502` on a refresh (the stored state is still served without it).
* A job in a final state never changes again, even if the appliance later forgets the task.
* If the appliance no longer knows a running task, the job becomes `FAILED` with the log line "The appliance no longer reports
  this task; result unknown."
* One unreachable appliance does not delay the others; its jobs are retried on the next round.

## Cancelling

`POST /backup-jobs/{id}/cancel` stops a `PENDING` or `RUNNING` job (`CANCELLED`, with what was transferred so far).
`409` if the job has finished. If it finished on the appliance just before the request, the real result is stored first and
the answer is still `409`, so a successful backup is never recorded as cancelled.

## Listing and reporting

* `GET /backup-jobs?status=FAILED&status=PARTIALLY_SUCCESSFUL&active=&backup_target_id=&asset_id=&policy_id=&limit=&offset=`,
  newest first, with `total`.
* `GET /backup-jobs/summary?window=24h|7d|30d&backup_target_id=`: jobs per status, how many are active, **success rate**
  (`SUCCESS` / (`SUCCESS` + `FAILED` + `PARTIALLY_SUCCESSFUL`); cancelled jobs are not counted), GB transferred and the average MB/s.

## Schedules

Every enabled policy runs on its `cron_schedule`. The scheduler (APScheduler) lives in the **worker** process
(`arq app.worker.settings.WorkerSettings`) and is switched on by default (`CMP_SCHEDULER_ENABLED`).

* **Time zone:** one zone for the whole system, `CMP_SCHEDULER_TIMEZONE` (default `Asia/Bangkok`). `"0 1 * * *"` means 01:00 in that
  zone. Change the setting to change all policies; there is no per-policy zone.
* **Cron format:** five fields, `minute hour day-of-month month day-of-week`, with `*`, lists, ranges and steps. Weekdays follow
  crontab: **0 and 7 are Sunday**, 1 is Monday; names (`mon-fri`) work too. These are refused when the policy is saved (`422`):
  values out of range, `L`/`W`/`#` extensions, and a schedule that restricts **both** day-of-month and day-of-week (a crontab runs
  on either day, APScheduler on both at once, so the result would not be what was meant). Use one of the two fields.
* **What a run does:** it starts a backup (the same `POST /backup-jobs` rules) for every asset assigned to the policy.
  An asset that already has a backup in progress is **skipped**; an asset that cannot be started (appliance unreachable,
  ...) is counted as **failed**; neither stops the other assets. At most `CMP_SCHEDULER_MAX_PARALLEL_STARTS` (4) are started
  at the same moment. The backup type is the policy's.
* **Changes** (new, edited, disabled or deleted policy) are picked up within `CMP_SCHEDULER_SYNC_SECONDS` (30).
* **Missed runs:** if the worker was down when a run was due, it is started only when the worker is back within
  `CMP_SCHEDULER_MISFIRE_GRACE_SECONDS` (300); after that the run is skipped, not made up later. The schedule is rebuilt from the
  database at every start, nothing else is stored.
* **Several workers:** each firing is claimed in Redis first (`SET NX`, key `cmp:sched:<policy>:<scheduled time>`), so only one
  worker starts the backups even if two run by mistake.
* **Seeing it:** `GET /backup-policies/{id}` shows `next_run_at`; `GET /backup-policies/{id}/schedule?count=5` lists the next runs
  (empty while the policy is disabled); each timed run writes an audit entry `schedule.run` (actor `scheduler`, with how many
  assets were started, skipped and failed): `GET /audit-logs?action=schedule.run`.
* **Run now:** `POST /backup-policies/{id}/run` does the same thing on demand (audit entry `policy.run`, actor = the API user) and
  answers with the result per asset. `409` if the policy is disabled.

The CMP starts these backups itself. It does **not** create a schedule on the appliance (see `reference-check.md`: the real
OceanProtect schedules through SLAs of its own), so a backup is never started twice by two schedulers.

## Audit

`backup.trigger`, `backup.cancel`, `asset.create/update/delete` and the policy changes are written to the audit log,
including refused attempts (outcome `failure`).
