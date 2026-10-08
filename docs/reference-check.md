# Check against the Huawei REST reference

The mock server and the driver were first written from general knowledge. They were then compared with
*OceanStor Dorado V700R001C20 REST Interface Reference (For Engineers)*, issue 02, 2026-03-30.

**Limit of this check:** that document is for Dorado, not OceanProtect. The DeviceManager conventions are shared, but an
OceanProtect appliance may differ, so everything below is "matches the Dorado reference", not "matches OceanProtect".
The reference could not be checked for a table of error codes: it has none.

## Matches the reference

* URL shape `https://{ip}:8088/deviceManager/rest/{deviceId}/{resource}`, login `POST .../xxxxx/sessions` with `username`,
  `password`, `scope`; `deviceid` and `iBaseToken` in the answer; envelope `{"data": ..., "error": {"code": 0, "description": "0"}}`.
* Capacities counted in 512-byte sectors, uint values as strings; `HEALTHSTATUS` 1 = normal, 2 = faulty; pool `RUNNINGSTATUS` 27 = online.
* `/controller` with `CPUUSAGE` and `MEMORYUSAGE`; `/disk`; `/storagepool`; `/alarm/currentalarm`.
* `range=[a-b]` with the end index excluded.

## Changed after the check

| Topic | Before | Now (reference) |
|---|---|---|
| Alarm `level` | 2 / 3 / 4 | **3 = warning, 5 = major, 6 = critical** (the driver would have shown a critical alarm as a warning) |
| Alarm ids | `eventID` was a hex string | `eventID` is a decimal number, `strEventID` the hex string (stored as `event_id`); `sequence` is a number; more fields (`alarmStatus`, `clearTime`, ...) |
| Alarm order | insertion | newest first (`startTime,d`) and `filter` on `level`, `startTime`, `sequence` |
| Session | token only, 30 minutes | **token header and session cookie** both required (else `-401`), **20 minutes** |
| Ids | 12 digits / 32 hex | `deviceid` 20 characters like `2102351...`, `iBaseToken` 64 hex digits |
| Login | `scope` optional | `scope` mandatory |
| `filter` | `KEY::value` only | `:` fuzzy, `::` exact, `and` / `or`, `key:[from,to]` ranges |
| Pool reduction | invented `LOGICALWRITTENCAPACITY`, `POSTDEDUPCAPACITY`, `DATAREDUCTION_RATIO` | documented `DEDUPINVOLVEDCAPACITY`, `DEDUPEDCAPACITY`, `COMPRESSINVOLVEDCAPACITY`, `COMPRESSEDCAPACITY`, `REDUCTIONINVOLVEDCAPACITY` and the ratios `DEDUPLICATIONRATE`, `COMPRESSIONRATE`, `SPACEREDUCTIONRATE` as JSON strings (`numerator / denominator`) |
| Degraded health | 3 | 5 (pool `HEALTHSTATUS`) |
| CPU / memory | numbers | strings |

The driver reads: ingested = `DEDUPINVOLVEDCAPACITY`, post-dedup = ingested - `DEDUPEDCAPACITY`, physical = `USERCONSUMEDCAPACITY`.
That is an interpretation of the field descriptions ("capacity for deduplication", "capacity saved by deduplication",
"used capacity"); verify it on a real appliance. On a pool that holds non-reducible data these may not add up the way the
backup formulas assume.

The driver is also tested with payloads copied from the reference (`backend/tests/test_reference_samples.py`).

## Still not verified (invented or unknown)

* **Error codes.** The numbers the mock emits (`1077948993` exists, `1077948996` not found, `1077948997` no space, ...) are not in
  the reference, so on a real appliance the driver will see unknown codes and report them as a generic error (HTTP 502), not as
  404 / 409. The code table is needed to fix this.
* **Performance.** The reference reads real-time performance through `performace_statistic/cur_statistic_data`
  (the misspelling is in the document) with an object UUID such as `207:0A` and indicator ids (for example 21 bandwidth, 22 IOPS).
  The mock's `/performancedata` is the simplified endpoint from the project brief; the driver must be changed to the documented
  call, and the indicator ids for OceanProtect throughput found in the appendix, before it can read a real appliance.
* `/nvram` (dedup cache), `/power`, the backup endpoints (`/backup_policy`, `/backup_job`, `/task_list`, `/asset`),
  `/worm_policy`, `/backup_retention`: not in the Dorado reference; OceanProtect's own interface is needed.
* The `Secure` attribute of the session cookie is left out in the mock because it runs over plain http.
* Session keep-alive: the mock offers `PUT /sessions`; the reference describes only login and `DELETE /sessions`.

---

# Second reference: OceanProtect DataBackup REST (V200R001C32)

The real OceanProtect reference (*OceanProtect Appliance V200R001C32 REST Interface Reference*, issue 01, 2026-04-30) was
read afterwards. **It describes a different interface from the DeviceManager style above, and from everything the mock server
and the driver currently speak.** The mock and the driver were written for the Dorado conventions and still follow them; they
have to be rebuilt on this reference (planned as the next piece of work). Only the headings, the authentication chapter and the
interfaces listed below were read; the rest of the 3,800-page document was not checked.

| Topic | OceanProtect reference | What the CMP has now |
|---|---|---|
| Address | `https://<ip>:25081/v1/...` | `http://<ip>:8088/deviceManager/rest/{deviceId}/...` |
| Login | `POST /v1/auth/token` with `{"userName", "password"}`; the answer holds the token; send it as `X-Auth-Token`; valid 24 hours | `POST .../sessions`, `iBaseToken` header plus cookie, 20 minutes |
| Errors | an HTTP status, and a body `{"errorCode", "errorMessage", "retryable", "parameters"}` | HTTP 200 with `error.code` |
| Manual backup | `POST /v1/protected-objects/{resource_id}/action/backup` with `{"action", "sla_id", "copy_name", "priority"}`; `action` is `full`, `log`, `cumulative_increment`, `difference_increment`, `permanent_increment`, `snapshot`, ... | `POST .../backup_job` |
| Job | `GET /v1/jobs/{jobId}`; status `READY, PENDING, RUNNING, SUCCESS, PARTIAL_SUCCESS, ABORTED, ABORTING, FAIL, ABNORMAL, CANCELLED, ABORT_FAILED`; `progress`, `speed`, `startTime`, `endTime`; `extendStr` holds `dataBeforeReduction` / `dataAfterReduction` / `slaName` / `backupType`; `POST /v1/jobs/{jobId}/action/stop`, `GET /v1/jobs/{jobId}/logs` | `.../task_list/{taskId}` with `PENDING, RUNNING, SUCCESS, FAILED, PARTIALLY_SUCCESSFUL, CANCELLED` |
| Schedules | **The appliance schedules by itself through SLAs**: `POST /v1/slas` with `policy_list`, each with `schedule` (`trigger` 1 periodic / 2 right after backup / 3 at a given time / 4 by period; `interval` + `interval_unit`, `start_time`, `window_start`/`window_end`, days of week/month), `retention` (including WORM period) and `action`. These are intervals and windows, **not cron** | cron kept in the CMP |
| Capacity | `totalCapacity`, `usedCapacity`, `freeCapacity`, `consumedCapacity`, `writeCapacity` (logical) and `spaceReductionRate`, all in KB | sectors, from `/storagepool` |
| Alarms | `GET /v1/alarms`; `severity` 1 warning, 3 major, 4 critical; `sequence` is a number | `level` 3 / 5 / 6 (Dorado) |
| Storage unit | `/v1/storage-units` (create with `deviceType`, `name`, `poolId`, `deviceId`) | - |

## What this means for the CMP

* **The scheduler does not depend on it.** Policy schedules run in the CMP and only call `trigger_backup` on the driver, so the
  scheduler keeps working unchanged when the driver is rebuilt.
* **Do not create SLAs from CMP policies yet.** An SLA with a schedule would make the appliance start backups on its own, in
  addition to the CMP's cron, and every backup would run twice. When SLAs are mapped, either the CMP schedule or the appliance
  schedule must be the only one.
* **Mapping to design in the rebuild:** CMP asset <-> protected object (`resource_id`), CMP policy <-> SLA (`sla_id`; a manual
  backup needs the SLA the object is protected with), CMP job status <-> the job states above (`FAIL` and `ABNORMAL` -> failed,
  `PARTIAL_SUCCESS` -> partially successful, `ABORTED` and `CANCELLED` -> cancelled, `READY` -> pending, `ABORTING` -> running).
* **Telemetry** needs the capacity, alarm and performance calls of this reference (`/v1/cluster/performance` exists) instead of
  the Dorado ones; the reduction ratio would come from `spaceReductionRate` and `extendStr` rather than from pool fields.
* Not yet read: the response of the manual backup call (which id it returns), error code tables, how `resource_id` is obtained
  (resource scan), the performance counters, WORM and copy retention calls.
