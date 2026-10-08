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
