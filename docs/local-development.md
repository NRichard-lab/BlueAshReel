# Local development

Docker Compose is the reference environment because it includes the correct FFmpeg package, migrations, runtime isolation, and reverse proxy. Native development is useful for shorter edit/test cycles.

## Compose workflow

Create configuration without starting services:

```powershell
.\scripts\bootstrap.ps1 -NoStart
docker compose config --quiet
```

```sh
sh scripts/bootstrap.sh --no-start
docker compose config --quiet
```

Then build and start:

```sh
docker compose up --detach --build
docker compose ps
docker compose logs --follow backend worker frontend proxy
```

Source directories are baked into images rather than live-mounted. Rebuild the affected service after code changes:

```sh
docker compose up --detach --build backend worker
docker compose up --detach --build frontend
```

The API is reached through `http://localhost:8080/api/v1`; the API container itself is not published to the host.

## Native backend

Requirements: Python 3.12, FFmpeg/FFprobe on `PATH`, and a compiler only if a dependency lacks a wheel.

```sh
cd backend
python -m venv .venv
# Linux: . .venv/bin/activate
# PowerShell: .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Set a development-only environment outside source control. Use an absolute media root and do not point writable data directories into it.

```powershell
$env:APP_SECRET_KEY = '<at-least-64-random-characters>'
$env:DATABASE_URL = 'sqlite:///./data/app.db'
$env:APP_DATA_DIR = './data'
$env:ARTWORK_DIR = './data/artwork'
$env:TEMP_DIR = './data/tmp'
$env:MEDIA_ROOTS = 'D:\Media'
$env:OUTBOUND_INTEGRATIONS_ENABLED = 'false'
$env:PRODUCT_CONFIG_FILE = '..\config\product.json'
alembic upgrade head
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Run the worker in a second activated terminal with the same environment:

```sh
python -m app.worker
```

## Native frontend

The frontend requires the Node version declared in `frontend/package.json` and pnpm through Corepack.

```sh
cd frontend
corepack enable
pnpm install --frozen-lockfile
pnpm run dev
```

Use relative `/api/v1` requests in browser code. The reference Compose proxy serves UI and API on one origin. For native development, Vite proxies `/api` to `http://127.0.0.1:8000`; set `BACKEND_ORIGIN` before `pnpm run dev` only when the native backend listens elsewhere. Do not hard-code a production or public API origin.

## Checks

Run the checks relevant to each change before using the full validation sequence:

```sh
cd backend
ruff check .
mypy app
pytest

cd ../frontend
pnpm run lint
pnpm run typecheck
pnpm run test
pnpm run build

cd ..
docker compose config --quiet
```

To test migrations against a clean disposable database, set `DATABASE_URL` to a file under a temporary directory, run `alembic upgrade head`, inspect the result, and remove only that verified temporary directory. Never point a test command at the configured household database.

## Local API conventions

- All public REST endpoints are below the prefix in `config/product.json` (currently `/api/v1`).
- Potentially large collections are paginated.
- Health responses do not return sensitive records or host paths.
- Background work is enqueued and polled; it is not performed inside HTTP requests.
- Tests use generated fixtures, never private media.
