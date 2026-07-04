# Repository Guidelines

## Project Structure & Module Organization

SIQ Research Engine is a mixed Python and TypeScript research workspace. Core applications live in `apps/`: `apps/web` is the React/Vite UI, `apps/api` is the FastAPI aggregation backend, and `apps/pdf-parser` plus `apps/document-parser` handle document parsing. Market services are in `services/market-report-finder` and `services/market-report-rules`. Shared Python contracts live in `packages/market-contracts`. Database DDL/DML and import utilities are under `db/`; operational scripts are in `scripts/`; Docker, model-service, and environment samples are in `infra/`. Keep generated runtime data in ignored paths such as `data/`, `var/`, `artifacts/`, and service-specific `test-results/` folders.

## Build, Test, and Development Commands

- `./start_all.sh` starts the local stack using `infra/env/local.env`.
- `docker compose -f infra/docker/docker-compose.yml --env-file infra/env/local.env up` runs the containerized stack.
- `scripts/check_all.sh` runs the main Python and frontend checks used for broad validation.
- `cd apps/web && npm run dev -- --host 0.0.0.0 --port 15173` starts the UI.
- `cd apps/web && npm run check:frontend` runs ESLint and the production build.
- `cd apps/api && uv sync --extra dev && uv run python -m pytest tests` runs API tests.

## Coding Style & Naming Conventions

Use Python 3.11+ for services and prefer typed Pydantic/FastAPI contracts at API boundaries. Python tests and modules use snake_case filenames such as `test_source_access.py`. Frontend code uses TypeScript, React 19, Vite, and ESLint; components use PascalCase, hooks use `use*`, and feature code belongs under `apps/web/src/features/<domain>`. Keep path and environment additions under the existing `SIQ_*` naming convention.

## Testing Guidelines

Python projects use `pytest` with tests in each package’s `tests/` directory. Add focused tests beside the module you change, and prefer deterministic fixtures over live external calls. Frontend unit tests run with `npm run test:unit`; Playwright specs live under `apps/web/e2e` and run with `npm run e2e` when UI behavior changes.

## Commit & Pull Request Guidelines

Recent history favors concise, scoped subjects such as `web: extract settings service counts`, `api: add shared job and command contracts`, and `docs: update v2 owner split plan`. Use an imperative summary and keep unrelated changes separate. Pull requests should describe the changed area, list validation commands, link issues or design notes, and include screenshots for visible UI updates.

## Security & Configuration Tips

Do not commit secrets or local runtime data. Start from `infra/env/local.example`, write local overrides to `infra/env/local.env`, and keep required secrets such as `SIQ_AUTH_SECRET_KEY` and `SIQ_SOURCE_TOKEN_SECRET` at least 32 characters.
