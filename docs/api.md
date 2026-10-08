# Backend REST API

Base path `/api/v1`. Interactive reference: `GET /docs` (Swagger) and `GET /openapi.json`.
`GET /health` needs no key.

## Authentication

Every `/api/v1` call needs the header `X-API-Key: <key>`. Keys come from `CMP_API_KEYS`
(`"alice:key1,bob:key2"`). The user name that owns the key is written as `actor` in the audit log.
Wrong or missing key: `401`. There are no roles yet: every key may do everything.

## Endpoints

| Group | Calls |
|---|---|
| Devices | `POST/GET /devices`, `GET/PATCH/DELETE /devices/{id}`, `POST /devices/{id}/test` |
| Telemetry | `GET /devices/{id}/capacity`, `/reduction-ratio`, `/alarms`, `/performance` |
| Tenants | `POST/GET /tenants`, `GET/PATCH/DELETE /tenants/{id}`, `GET /tenants/{id}/usage` |
| Volumes (LUN) | `POST/GET /volumes`, `GET/DELETE /volumes/{id}`, `POST /volumes/{id}/expand`, `/map`, `/snapshots` |
| File systems | `POST/GET /filesystems`, `GET/DELETE /filesystems/{id}`, `POST /filesystems/{id}/quota`, `/snapshots` |
| Buckets | `POST/GET /buckets`, `GET/DELETE /buckets/{id}`, `PUT /buckets/{id}/quota`, `POST /buckets/credentials` |
| Policies | `POST/GET /protection-policies`, `GET/PATCH/DELETE /protection-policies/{id}` |
| Audit | `GET /audit-logs?actor=&action=&resource_type=&outcome=&storage_device_id=&tenant_id=&since=&until=&limit=&offset=` |

List calls take `limit` (1-500, default 100) and `offset`; volumes, file systems and buckets also filter by
`tenant_id` and `storage_device_id`.

## Behaviour worth knowing

* **Credentials**: `POST /devices` takes `username` and `password`; they are encrypted (AES-256-GCM) and never
  returned. `PATCH` may change either one on its own.
* **`POST /devices/{id}/test`** logs in to the array and reads its alarms. It always answers `200`; look at
  `reachable`. It stores `device_id`, `health_status` (`healthy`, `degraded` on major/critical alarms, `fault`
  when unreachable) and `last_seen_at`.
* **Order of work**: the array is changed first, then the CMP database. If the database write fails, the new
  array resource is removed again (best effort, logged when that fails too). Deleting something that no longer
  exists on the array only cleans the database.
* **Tenant quotas** are checked before every create or grow: block = sum of LUN sizes, file = sum of file system
  sizes, object = sum of bucket quotas (buckets therefore need `quota_gb`). A tenant with quota `0` can create
  nothing; an inactive tenant cannot create anything. Failure: `409`.
* **Protection policies**: `protection_policy_id` on a volume is stored only. On a file system of an OceanProtect
  device, a policy with `worm_mode` other than `none` also creates the WORM policy on the array. A WORM policy on
  a Dorado device is refused (`422`).
* **S3 credentials** (`POST /buckets/credentials`): the secret key is in this one response only; it is not stored
  and not written to the audit log.
* **Mapping** a volume creates a LUN group and a host group/mapping view on the array. A volume can be mapped once.

## Errors

| Status | When |
|---|---|
| 401 | missing/wrong API key |
| 404 | unknown CMP object, or the array reports the object missing |
| 409 | name already used, quota exceeded, object in use (for example a mapped LUN), WORM/retention lock, inactive tenant, device or tenant still owns resources |
| 422 | invalid body, or the product does not support the operation (LUN on OceanProtect, bucket on OceanProtect, WORM on Dorado) |
| 502 | array unreachable or rejected the stored credentials |

Array errors carry `device_error_code`.

## Audit log

Each state-changing call (and `device.test`) writes one row: who (`actor`), what (`action`, for example
`volume.create`), which object (`resource_type/id/name`, `storage_device_id`, `tenant_id`), `outcome`
(`success` or `failure`), `details` (sizes, error text) and `source_ip`. Failed calls are recorded too.
Rows are kept when the device or tenant is deleted. Read calls are not logged.
