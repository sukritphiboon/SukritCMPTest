# Mock OceanProtect API

Run: `uvicorn mock_server.main:app --port 8088` (environment: `MOCK_SEED`, `MOCK_USERNAME` = admin,
`MOCK_PASSWORD` = Admin@storage1, `MOCK_MODEL`, `MOCK_RAW_TB` = 500, `MOCK_USED_PERCENT` = 40, `MOCK_DAILY_GROWTH_GB` = 800).

> Checked against the *OceanStor Dorado V700R001C20 REST Interface Reference* (see `reference-check.md`): login, session rules,
> filters, alarms, pool and controller fields follow it. Still invented: error codes, `performancedata`, `nvram`, `power`,
> `backup_policy`, `backup_job`, `task_list`, `asset`, `worm_policy` and `backup_retention`. Verify against a real appliance.

Every answer is HTTP 200 with `{"error": {"code": 0, "description": "0"}, "data": ...}`; empty lists omit `data`.
Capacities are strings counted in 512-byte sectors. Login: `POST /deviceManager/rest/xxxxx/sessions` returns `iBaseToken` and
`deviceid` (20 characters) and sets the cookie `session=ismsession=...`; **every later request needs both** the `iBaseToken` header
and that cookie (else `-401`) and uses `/deviceManager/rest/{deviceid}/...`. Sessions last **20 minutes**; `PUT /sessions` extends one.
List calls accept `filter` (`k::v` exact, `k:v` fuzzy, `k:[from,to]` range, joined by `and` / `or`) and `range=[0-100]` (end excluded).

| Area | Endpoints |
|---|---|
| Session | `POST/GET/PUT/DELETE /sessions` |
| System | `GET /system/` |
| Pool | `GET /storagepool`: raw, consumed (physical), `LOGICALWRITTENCAPACITY`, `POSTDEDUPCAPACITY`, `DEDUPRATIO` (fraction), `DEDUPFACTOR`, `COMPRESSIONRATIO`, `DATAREDUCTION_RATIO` (20-42:1). Physical use grows with (simulated) time |
| Performance | `GET /performancedata`: write 2500-8000 MB/s, read 400-2500 MB/s, IOPS 8k-40k, streams per controller |
| Hardware | `GET /controller` (CPU, memory), `/nvram` (dedup cache), `/power` (4 modules), `/disk` (24) |
| Alarms | `GET /alarm/currentalarm`: `level` 6 critical, 5 major, 3 warning; `eventID` decimal, `strEventID` hex; newest first |
| Backup | `/backup_policy` (CRUD), `/asset`, `POST /backup_job` (returns `taskId`), `GET /task_list/{taskId}`, `PUT /task_list/{taskId}/cancel` |
| Immutability | `/filesystem`, `/worm_policy` (compliance mode cannot be removed), `/backup_retention` (locked copies cannot be deleted) |

Task lifecycle: `PENDING` (3 s) -> `RUNNING` (60-600 s, progress grows) -> `SUCCESS`, `FAILED` or `PARTIALLY_SUCCESSFUL`
(about 82 / 8 / 10 %), or `CANCELLED`. `POST /backup_job` accepts `SIZEGB` and, for tests only, `SIMULATE_OUTCOME`.

Simulation controls (no authentication): `POST /_mock/reset`, `/_mock/advance_time {"seconds"}`, `/_mock/alarms {"level","name"}`,
`DELETE /_mock/alarms/{sequence}`, `/_mock/ingest {"logical_gb"}`, `/_mock/performance {"write_mbps","read_mbps","iops",
"streams_per_controller"}` (pin values; `{}` unpins), `/_mock/hardware_fault {"component","id","health": ok|degraded|fault}`,
`/_mock/next_backup {"outcome","size_gb","duration_s"}` (decides the next backup task), `/_mock/lose_task {"task_id"}`, `GET /healthz`.

Error codes (simulated): `-401` not logged in, `50331651` bad parameter, `1077948993` exists, `1077948996` not found,
`1077948995` in use, `1077948997` no space, `1077949061` bad credentials, `1077936900` WORM locked.
These numbers are **not** from the reference.
