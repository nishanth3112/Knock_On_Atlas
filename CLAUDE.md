# Knock-On Atlas

SEC supplier-exposure graph and 8-K analysis with LangGraph agents. Python 3.12, Postgres/pgvector, FastAPI, FastMCP, AWS Lambda.

## How to work

These rules favor care over speed; for trivial tasks, use judgment.

**Think before coding.** State your assumptions. If a request has two readings, name both and ask. If something is unclear, stop and say what is confusing. Push back when a simpler approach exists.

**Simplicity first.** Write the minimum code that solves the problem. No unrequested features, abstractions or configuration, and no error handling for impossible cases. If 200 lines could be 50, rewrite.

**Surgical changes.** Touch only what the task needs and match the existing style. Don't refactor, reformat or re-comment nearby code. Remove only what your own change made unused.

**Goal-driven execution.** Turn each task into a check that proves it: a failing test, then the fix. For multi-step work, state a short plan with a check per step. Iterate until it passes; report what ran.

## Project rules

- Use uv for everything; never pip or venv.
- Every external service has a fake, so tests run offline.
- SEC requests use the env User-Agent, at most 5 per second, cached.
- Only ingestion or a reviewer writes `entity_edges`, never an agent or MCP tool.
- Numbers in docs come from eval output files, never typed by hand.
- Never commit data, secrets or `.env`.
- Check library APIs against the installed version.

## Commands

- Tests: `uv run pytest`
- Lint and types: `uv run ruff check . && uv run mypy src`
- Local services: `docker compose up -d`
