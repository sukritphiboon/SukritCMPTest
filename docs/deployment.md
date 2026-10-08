# Deployment

## Local stack

```bash
cp .env.example .env
python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"   # paste into CMP_ENCRYPTION_KEY
docker compose up --build
```

| Service | Port | Notes |
|---|---|---|
| postgres | internal | volume `pgdata` |
| redis | internal | ARQ queue |
| mock-dorado | 8088 | simulated Dorado V7 |
| mock-oceanprotect | 8089 | simulated OceanProtect |
| backend | 8000 | runs `alembic upgrade head` on start |
| worker | - | `arq app.worker.settings.WorkerSettings`, polls devices every 5 minutes |
| frontend | 3000 | skeleton page |

Register mock arrays with host `mock-dorado` / `mock-oceanprotect`, `https=false`, user `admin`,
password `Admin@storage1`.

## Keys and secrets

* Losing `CMP_ENCRYPTION_KEY` makes stored device credentials unreadable. Keep it in a secret store and back it up.
* Rotating the key needs a script that decrypts with the old key and re-encrypts (not written yet).
* Set a strong `POSTGRES_PASSWORD`; the compose defaults are for development only.

## Tests

```bash
pip install -e "mock-server[dev]" -e "backend[dev]"
make test
```
Backend model tests use SQLite; the migration is generated and checked against SQLite, so run
`alembic upgrade head` once against a real PostgreSQL before relying on it.
