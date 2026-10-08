# Architecture

## Layers

```
frontend (Next.js)  ->  backend API (FastAPI)  ->  services  ->  StorageDriverBase  ->  array REST API
                                   |                                  |
                              PostgreSQL                  DoradoV7Driver / OceanProtectDriver
                              Redis + ARQ worker (polling, long jobs)
```

* **Services** (to be written) know only `StorageDriverBase` and the database models.
* **Drivers** (`backend/app/drivers`) are adapters: they translate the neutral calls into one product's
  REST dialect and translate answers into neutral objects (`backend/app/schemas/storage.py`).
* `create_driver(model, connection)` picks the adapter from `StorageDevice.model`.

## Driver interface

| Group | Methods |
|---|---|
| Block | `create_lun`, `delete_lun`, `expand_lun`, `create_lun_group`, `map_to_host` |
| Snapshots | `create_snapshot(resource_id, name, resource_type="lun"\|"filesystem")` |
| File | `create_filesystem`, `create_share("nfs"\|"cifs")`, `set_quota` |
| Object | `create_bucket`, `set_bucket_quota`, `generate_s3_credentials` |
| Telemetry | `get_capacity_metrics`, `get_reduction_ratio`, `get_active_alarms`, `get_performance_metrics` |

Class tree: `StorageDriverBase` -> `HuaweiDeviceManagerDriver` (login, file, telemetry) ->
`DoradoV7Driver` (adds block + object) and `OceanProtectDriver` (block/object raise `NotSupportedError`;
adds `create_worm_policy`, `list_backup_copies`).

Sizes are GB in the neutral API; the Huawei client converts to 512-byte sectors.

## Data model

`StorageDevice`, `Tenant`, `StorageVolume`, `FileSystem`, `ObjectBucket`, `ProtectionPolicy`, `AuditLog`
(`backend/app/models`). Notes:

* `StorageDevice.device_id` is the id reported by the array; foreign keys to a device are named
  `storage_device_id` to avoid confusion with it.
* Device credentials are one JSON document encrypted with **AES-256-GCM** (`app/core/crypto.py`);
  the row id is used as authenticated data, so a ciphertext copied to another row will not decrypt.
  The key is `CMP_ENCRYPTION_KEY` (base64 of 32 random bytes).
* `AuditLog` rows are kept when a device or tenant is deleted (foreign keys use `SET NULL`).
* Enums are stored as VARCHAR (no native PostgreSQL enum types).
* Tenant quotas are stored but not yet enforced by any service.
