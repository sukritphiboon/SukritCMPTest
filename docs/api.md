# Backend REST API

Base path `/api/v1`. Interactive reference: `GET /docs`. `GET /health` needs no key.
The analytics endpoints are described in `telemetry.md`, backups in `backup-orchestration.md`.

## Authentication

Send `X-API-Key: <key>`. Keys come from `CMP_API_KEYS` (`"alice:key1,bob:key2"`); the user name is written as `actor` in the audit
log. Missing or wrong key: `401`. There are no roles yet: every key may do everything.

## Targets (appliances)

| Call | Notes |
|---|---|
| `POST /targets` | `name, ip_address, management_port (8088), username, password, model (x3000, x6000, x8000, x9000)`. The password is encrypted and never returned |
| `GET /targets`, `GET/PATCH/DELETE /targets/{id}` | `PATCH` can change username or password alone |
| `POST /targets/{id}/test` | Logs in and reads the identity (fills serial number, appliance id, firmware). Always `200`; check `reachable` |

## Backups

| Call | Notes |
|---|---|
| `POST/GET /backup-policies`, `GET/PATCH/DELETE /backup-policies/{id}` | cron, full/incremental, retention, WORM (`worm_enabled` needs an explicit `worm_mode`) |
| `POST/GET /assets`, `GET/PATCH/DELETE /assets/{id}` | registered on the appliance as well; `PATCH {"policy_id": null}` removes the policy |
| `POST /backup-jobs` | `202`, starts a backup |
| `GET /backup-jobs`, `GET /backup-jobs/{id}?refresh=`, `GET /backup-jobs/summary` | list, follow, report |
| `POST /backup-jobs/{id}/cancel` | stops a running job |

## Audit log

`GET /audit-logs?actor=&action=&resource_type=&outcome=&backup_target_id=&since=&until=&limit=&offset=`.
Every change (and `target.test`, `alarm.acknowledge`) writes one row with `actor`, `action`, the object, `outcome`
(`success` / `failure`), details and source IP. Failed calls are recorded too. Reads are not logged. Secrets are never written.

## Errors

| Status | When |
|---|---|
| 401 | missing/wrong API key |
| 404 | unknown object, or the appliance reports it missing |
| 409 | name already used, object in use, WORM / retention lock |
| 422 | invalid body or query (for example `window=5m`) |
| 502 | appliance unreachable or it rejected the stored credentials |

Appliance errors carry `device_error_code`.
