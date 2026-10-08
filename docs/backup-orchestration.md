# Backup orchestration

Start backups on an appliance, follow them until they end, cancel them, and see how they went.

## Objects

| Object | Where it lives | Notes |
|---|---|---|
| Policy (`/backup-policies`) | CMP only | cron schedule, full or incremental, retention days, WORM switch and mode |
| Asset (`/assets`) | CMP **and** appliance | registering one also creates it on the appliance (`array_asset_id`); deleting removes both |
| Job (`/backup-jobs`) | CMP, mirrors an appliance task | keeps its history when the asset or policy is deleted |

A policy is **not** pushed to the appliance yet. Today it decides the backup type of a job and is the place where schedule,
retention and WORM are kept. Applying retention/WORM on the appliance and running the cron schedule (APScheduler) come later.

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

## Audit

`backup.trigger`, `backup.cancel`, `asset.create/update/delete` and the policy changes are written to the audit log,
including refused attempts (outcome `failure`).
