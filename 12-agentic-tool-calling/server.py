import uuid
import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from agents import Runner
from server_agents import agent
from utils import encode_sse, to_dict, synthesize_tool_result_text


# ------------------------------- Shared task state
async def publish(ev: str, data: dict):
    await events_q.put(encode_sse(ev, data))


@dataclass
class Task:
    id: str
    items: List[Any]
    todos: List[dict] = field(default_factory=list)
    status: str = "running"


tasks: Dict[str, Task] = {}
events_q: asyncio.Queue[bytes] = asyncio.Queue()


# -------------------------------------- App
app = FastAPI()

# CORS (safe even when serving UI; convenient for dev)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static UI at /ui (index at /ui/). Keep POST / for chat.
app.mount("/ui", StaticFiles(directory="ui", html=True), name="ui")


@app.get("/")
def root_to_ui():
    return RedirectResponse(url="/ui/")


# ------------------------------------ Chat API (POST /)
@app.post("/")
async def chat_endpoint(request: Request):
    body = await request.json()
    conversation_id = body.get("conversationId")

    async def event_stream():
        run = Runner.run_streamed(
            agent,
            input=body.get("items", []),
            previous_response_id=body.get("previousResponseId"),
            conversation_id=conversation_id,
        )
        async for ev in run.stream_events():
            if ev.type == "raw_response_event":
                data = to_dict(ev.data)
                if isinstance(data, dict) and data.get("type") == "function.tool_result":
                    # log friendly output
                    try:
                        import json as _json

                        print("[tool_result]", data.get("name"), _json.dumps(data.get("result"), ensure_ascii=False))
                    except Exception:
                        print("[tool_result]", data)
                    # also synthesize a short human-friendly line
                    text = synthesize_tool_result_text(str(data.get("name")), data.get("result"))
                    yield encode_sse(
                        "raw_response_event",
                        {"type": "synthesized.message", "text": text},
                    )
                yield encode_sse(ev.type, data)
        yield encode_sse("done", {})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ------------------------------------ Tasks API
async def worker(task: Task, prev_id: Optional[str], conversation_id: Optional[str]):
    run = Runner.run_streamed(
        agent,
        input=task.items,
        previous_response_id=prev_id,
        conversation_id=conversation_id,
        context=task,
        max_turns=100,
    )
    async for ev in run.stream_events():
        if ev.type == "raw_response_event":
            data = ev.data.to_dict() if hasattr(ev.data, "to_dict") else getattr(ev.data, "__dict__", {})
            if not data:
                data = to_dict(ev.data)
            # Console hint for tool results
            if isinstance(data, dict) and data.get("type") == "function.tool_result":
                try:
                    import json as _json

                    print(f"[tool_result task={task.id}]", data.get("name"), _json.dumps(data.get("result"), ensure_ascii=False))
                except Exception:
                    print(f"[tool_result task={task.id}]", data)
            await publish("task.updated", {"task_id": task.id, "event": data})
            # friendly synthesized line
            if isinstance(data, dict) and data.get("type") == "function.tool_result":
                text = synthesize_tool_result_text(str(data.get("name")), data.get("result"))
                await publish(
                    "task.updated",
                    {"task_id": task.id, "event": {"type": "synthesized.message", "text": text}},
                )

    task.status = "done"
    await publish("task.updated", {"task_id": task.id, "status": "done"})


@app.post("/tasks")
async def post_create_task(req: Request):
    body = await req.json()
    items = body.get("items", [])
    previous_response_id = body.get("previousResponseId")
    conversation_id = body.get("conversationId")

    t = Task(id=uuid.uuid4().hex, items=items)
    tasks[t.id] = t
    await publish("task.created", {"task": {"id": t.id}})

    asyncio.create_task(worker(t, previous_response_id, conversation_id))
    return {"task_id": t.id}


@app.get("/events")
async def get_events(req: Request):
    async def gen():
        while True:
            chunk = await events_q.get()
            yield chunk
            if await req.is_disconnected():
                break

    return StreamingResponse(gen(), media_type="text/event-stream")


if __name__ == "__main__":
    import argparse, os
    import uvicorn

    parser = argparse.ArgumentParser(description="Combined chat + tasks + UI server")
    parser.add_argument("--host", default=os.getenv("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")))
    parser.add_argument("--reload", action="store_true", default=os.getenv("RELOAD", "false").lower()=="true")
    args = parser.parse_args()

    uvicorn.run("server:app", host=args.host, port=args.port, reload=args.reload)
