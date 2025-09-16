# Repository Guidelines

## Project Structure & Module Organization
- `server.py`: FastAPI app (chat + tasks + static UI) with SSE streaming.
- `server_agents.py`: tool‑equipped `Agent` and model settings.
- `agents/`: lightweight stubs for `Agent`, `Runner`, and `function_tool` used by demos.
- `ui/` (`index.html`, `app.js`): browser UI served by `server.py` at `/ui/`.
- `utils.py`: streaming helpers, SSE encoding, CLI run loop, result summarizers.
- `mock_api.py`: in‑memory data used by tools; for development only.
- Demo scripts: `0_task.py`, `1_agent.py`, `2_tools.py`, `6_delegation.py`.
- `requirements.txt`: Python dependencies. No `tests/` yet.

## Build, Test, and Development Commands
- Create env: `python -m venv .venv && source .venv/bin/activate`
- Install deps: `pip install -r requirements.txt`
- Run dev server: `python server.py --reload --port 8000` → open `http://localhost:8000/ui/`.
- Run CLI demos: `python 2_tools.py` or `python 1_agent.py` (set `AGENT_MODEL=o3` to override).
- Debug stream: `AGENT_DEBUG_EVENTS=1 python 2_tools.py`.

## Coding Style & Naming Conventions
- Python 3.10+; 4‑space indent; PEP 8; add type hints and docstrings.
- Tool functions: snake_case verbs, include a concise docstring, decorate with `@function_tool`, and add to the `Agent.tools` list (see `server_agents.py`).
- If a new tool returns structured data, add a friendly summarizer in `utils.synthesize_tool_result_text`.
- Modules: snake_case; classes: CapWords; keep functions focused (<100 lines when reasonable).

## Testing Guidelines
- No automated tests yet. Prefer `pytest` with files under `tests/` named `test_*.py`.
- Run: `pytest -q` (after `pip install pytest`).
- Manual checks: `curl -N http://localhost:8000/events` for SSE; POST chat `curl -X POST http://localhost:8000/ -H 'Content-Type: application/json' -d '{"items":["hello"]}'`.

## Commit & Pull Request Guidelines
- Commit style mirrors history: imperative, descriptive titles (e.g., “Refactor agentic tool calling server”). Keep bodies wrapped at ~72 chars and explain why + what.
- PRs: include a clear summary, validation steps (commands to run), linked issues, and screenshots/GIFs for `ui/` changes. Keep PRs small and focused.

## Security & Configuration Tips
- Do not commit secrets. `secrets_config.py` is git‑ignored; set `OPENAI_API_KEY` there or via env. `bootstrap_secrets.py` auto-loads it.
- CORS is permissive in dev (`server.py`). Tighten before production and restrict origins.
- `mock_api.py` is for demos only—do not rely on it in production paths.

