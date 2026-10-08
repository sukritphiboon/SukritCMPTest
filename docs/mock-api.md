# Mock server API

Run: `MOCK_PROFILE=dorado|oceanprotect MOCK_SEED=1 uvicorn mock_server.main:app --port 8088`

> The paths and field names follow the style of the Huawei DeviceManager REST API but were written from
> general knowledge, not checked against the official reference. Entries marked *(simulated)* are inventions
> of this project. Verify against a real array before relying on them.

Envelope for every response (HTTP 200): `{"error": {"code": 0, "description": "0"}, "data": ...}`.
Empty lists omit `data`. Capacities are strings counted in 512-byte sectors.

Login: `POST /deviceManager/rest/xxxxx/sessions` with `{"username","password","scope":"0"}` returns `iBaseToken`
and `deviceid`. Send `iBaseToken` as a header and use `/deviceManager/rest/{deviceid}/...` afterwards.
Environment: `MOCK_USERNAME` (admin), `MOCK_PASSWORD` (Admin@storage1). Sessions last 30 minutes.

| Area | Endpoints | Dorado | OceanProtect |
|---|---|---|---|
| Session | `POST/GET/DELETE /sessions` | yes | yes |
| System | `GET /system/` | yes | yes |
| Pool | `GET /storagepool` (capacity, SmartThin/Dedupe/Compression, `DATAREDUCTION_RATIO`) | 3-12 : 1 | 20-42 : 1 |
| Block | `/lun`, `/lun/expand`, `/lungroup(+/associate)`, `/host`, `/hostgroup(+/associate)`, `/mappingview(+/create_associate)` | yes | `NOT_SUPPORTED` |
| Snapshot | `/snapshot` (LUN or file system) | yes | file system only |
| File | `/filesystem`, `/NFSHARE`, `/CIFSHARE`, `/FS_QUOTA` (bytes) | yes | yes |
| Protection | `/worm_policy`, `/backup_retention` *(simulated)* | `NOT_SUPPORTED` | yes |
| Alarms | `GET /alarm/currentalarm` | yes | yes |
| Performance | `GET /performance_statistic/cur_statistic_data` *(simplified)* | 300k-1.2M IOPS, <1 ms | 8-40k IOPS, 6-20 GB/s |
| Object | `/s3/...` and `/s3/_admin/...` *(simulated admin API)* | yes | 501 / `NOT_SUPPORTED` |

List calls accept `filter=KEY::VALUE` and `range=[0-100]`.

S3: standard bucket calls (`GET /s3/`, `PUT|HEAD|DELETE /s3/{bucket}`) answer in XML and expect an
`Authorization: AWS <access_key>:<anything>` header (signatures are not checked). Admin calls use `iBaseToken`:
`POST /s3/_admin/credentials`, `POST|GET /s3/_admin/buckets`, `PUT /s3/_admin/buckets/{bucket}/quota`.
Objects are not stored.

WORM: compliance mode policies and file systems cannot be removed; backup copies inside their retention
window cannot be deleted.

Simulation controls (no auth): `POST /_mock/reset`, `POST /_mock/advance_time {"seconds": n}`,
`POST /_mock/alarms {"level","name"}`, `GET /healthz`.

Error codes (simulated): `-401` not logged in, `50331651` bad parameter, `1077948993` exists,
`1077948996` not found, `1077948995` in use, `1077948997` no space, `1077949004` not supported,
`1077949061` bad credentials, `1077936900` WORM locked.
