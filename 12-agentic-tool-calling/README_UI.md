UI for Agentic Tool Calling

Quick start (single port)
- Run combined server: `python server.py --reload --port 8000`
- Visit the UI: `http://localhost:8000/ui/` (or just `/` which redirects)
- The UI defaults to the same origin for both Chat and Tasks bases, so no manual config is needed.

Notes
- The combined server includes permissive CORS for development and serves the UI statically.
- Chat window streams via POST + SSE parsing; Tasks uses GET `/events` via `EventSource`.

Files
- `server.py`: combined FastAPI app (chat + tasks + UI on one port)
- `ui/index.html`: Tailwind-based UI with Chat and Tasks panels
- `ui/app.js`: SSE streaming for chat, `EventSource` for tasks, live TODO rendering
