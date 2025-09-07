from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from utils import encode_sse, to_dict, synthesize_tool_result_text
from agents import Runner
import uvicorn
from server_agents import agent

app = FastAPI()


@app.post("/")
async def endpoint(request: Request):
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
                # Console + friendly message for synthetic tool results
                if isinstance(data, dict) and data.get("type") == "function.tool_result":
                    try:
                        import json as _json
                        print("[tool_result]", data.get("name"), _json.dumps(data.get("result"), ensure_ascii=False))
                    except Exception:
                        print("[tool_result]", data)
                    # Also emit a friendly synthesized line as an additional raw event
                    text = synthesize_tool_result_text(str(data.get("name")), data.get("result"))
                    yield encode_sse(
                        "raw_response_event",
                        {"type": "synthesized.message", "text": text},
                    )
                yield encode_sse(ev.type, data)
        yield encode_sse("done", {})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
