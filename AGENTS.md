# Repository Guidelines

## Project Structure & Module Organization

Northstar Quant is a research and simulated-trading workbench. `frontend/src/` contains React/TypeScript views, shared components, API helpers, and CSS; `frontend/public/` holds static assets. `backend/app/` contains the FastAPI API (`main.py`), task worker (`worker.py`), backtesting engine, strategy interpreter, and provider adapters. Tests live in `backend/tests/` and `frontend/tests/`. Deployment files are in `deploy/` and `compose.yaml`; architecture and acceptance guidance are in `docs/`. Runtime snapshots belong in ignored `data/`; frontend builds go to ignored `dist/`.

## Build, Test, and Development Commands

Run commands from the repository root, using an activated Python virtual environment:

- `pip install -r backend/requirements.txt` and `npm install`: install backend and frontend dependencies.
- `PYTHONPATH=backend uvicorn app.main:app --host 127.0.0.1 --port 8000`: start the API.
- `PYTHONPATH=backend python -m app.worker`: run the task worker separately.
- `npm run dev`: start Vite; `/api` requests proxy to port 8000.
- `npm run typecheck`: check strict TypeScript types.
- `npm run build`: type-check and build the frontend.
- `npm test`: run frontend tests using Node’s built-in test runner.
- `PYTHONPATH=backend python -m pytest backend/tests -q`: run backend tests.
- `docker compose up -d --build`: build and start PostgreSQL, API, worker, and web services.

## Coding Style & Naming Conventions

Use four-space indentation and `snake_case` for Python functions/modules. Use two-space indentation, `camelCase` helpers, and `PascalCase` React components and component filenames. Match nearby formatting; no dedicated formatter or linter is configured. Keep API schemas and frontend types synchronized when changing contracts.

## Testing Guidelines

Name pytest files `test_*.py` and frontend tests `*.test.mjs`. Add behavior-focused regression tests for changed logic, especially accounting, data provenance, strategy restrictions, and approvals. Backend fixtures use in-memory SQLite; they do not establish PostgreSQL concurrency correctness. No numerical coverage threshold is configured. Follow `docs/ACCEPTANCE.md` for manual workflows.

## Commit & Pull Request Guidelines

Git history is unavailable in this checkout, so commit conventions cannot be verified. Use concise imperative subjects, such as `Fix backtest fee accounting`. PRs should explain the behavior change, link relevant issues, list validation results, and include screenshots for UI changes. Update affected contracts and documentation together.

## Security & Configuration

Keep credentials in ignored `.env`/`.env.models`; never commit secrets or market snapshots. Export required variables for local Python processes, which do not automatically load `.env`. Preserve restricted strategy execution, explicit human approvals, and disabled real-order submission.
