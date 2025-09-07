import json
import dataclasses
from agents import Agent, Runner
from openai.types.responses import (
    ResponseOutputItemAddedEvent,
    ResponseOutputItemDoneEvent,
    ResponseCompletedEvent,
)
import asyncio
import json
import dataclasses
from fastapi import Request
from typing import Callable, Dict, Any, List
from functools import wraps
import inspect
from openai import OpenAI

# Load OPENAI_API_KEY from local secrets if available
try:  # noqa: F401
    import bootstrap_secrets  # type: ignore
except Exception:
    pass

COLOR_MAP = {
    "red": "31",
    "green": "32",
    "yellow": "33",
    "blue": "34",
    "magenta": "35",
    "cyan": "36",
    "white": "37",
    "gray": "90",
    "reset": "0",
}


def color(text, color_name):
    color_code = COLOR_MAP.get(color_name, COLOR_MAP["reset"])
    return f"\033[{color_code}m{text}\033[0m"


def synthesize_tool_result_text(name: str, result) -> str:
    """Return a short, human-friendly line summarizing a tool result."""
    try:
        n = name or "tool"
        if n == "get_emails" and isinstance(result, list):
            count = len(result)
            if count == 0:
                return "No matching emails found."
            subjects = [e.get("subject") for e in result if isinstance(e, dict)]
            subjects = [s for s in subjects if s]
            head = f"Found {count} email" + ("s" if count != 1 else "")
            if subjects:
                preview = "; ".join(subjects[:2])
                return f"{head}. Subjects: {preview}."
            return f"{head}."

        if n == "search_policies" and isinstance(result, list):
            if not result:
                return "No relevant policies found."
            titles = [p.get("title") for p in result if isinstance(p, dict) and p.get("title")]
            if titles:
                first = "; ".join(titles[:2])
                return f"Matched {len(result)} polic" + ("ies" if len(result)!=1 else "y") + f": {first}."
            return f"Matched {len(result)} policies."

        if n == "send_email" and isinstance(result, dict):
            to_addr = result.get("to") or result.get("to_addr")
            subj = result.get("subject")
            if to_addr and subj:
                return f"Sent email to {to_addr} with subject '{subj}'."
            return "Email sent."

        if n == "search_open_tickets" and isinstance(result, list):
            if not result:
                return "No open tickets matched."
            titles = [t.get("title") for t in result if isinstance(t, dict) and t.get("title")]
            if titles:
                return (
                    f"Found {len(result)} open ticket" + ("s" if len(result)!=1 else "") +
                    f": {titles[0]}" + ("; " + titles[1] if len(titles)>1 else "") + "."
                )
            return f"Found {len(result)} open tickets."

        if n == "add_ticket_comment":
            if result is None:
                return "Could not add comment (ticket not found)."
            if isinstance(result, list):
                return f"Comment added. Total comments: {len(result)}."
            return "Comment added."

        if n == "write_document" and isinstance(result, dict):
            did = result.get("id")
            title = result.get("title")
            if did and title:
                return f"Saved document {did}: {title}."
            return "Document saved."

        if n in {"read_document", "get_runbook_by_category"}:
            if not result:
                return "No runbook found."
            if isinstance(result, dict):
                title = result.get("title", "runbook")
                return f"Opened {title}."

        if n == "get_weather" and isinstance(result, dict):
            city = result.get("city", "")
            temp = result.get("temperature", "")
            cond = result.get("condition", "")
            parts = ", ".join([p for p in [temp, cond] if p])
            if city and parts:
                return f"Weather in {city}: {parts}."
            return "Weather retrieved."

        if n == "get_time" and isinstance(result, dict):
            loc = result.get("location_resolved") or result.get("tz") or "location"
            fmt = result.get("formatted")
            abbr = result.get("abbr") or ""
            if fmt and abbr:
                return f"Time in {loc}: {fmt} ({abbr})."
            if fmt:
                return f"Time in {loc}: {fmt}."
            return f"Time in {loc} available."
    except Exception:
        pass

    try:
        return f"{name} result: " + json.dumps(result, ensure_ascii=False)[:200]
    except Exception:
        return f"{name} result: {result}"


def run_demo_loop(agent: Agent):
    import sys

    conversation_id = None

    async def main():
        previous_response_id = None
        while True:
            try:
                user_input = input("> ")
            except (EOFError, KeyboardInterrupt):
                print("\nExiting.")
                break
            if user_input.strip().lower() in {"exit", "quit"}:
                print("Exiting.")
                break
            # CLI helpers for conversation management
            if user_input.strip().lower() == "/new":
                nonlocal conversation_id
                conversation_id = None
                previous_response_id = None
                print("(started a new conversation)")
                continue
            if user_input.strip().lower() == "/id":
                print(f"conversationId: {conversation_id or '(none)'}")
                continue

            run = Runner.run_streamed(
                agent,
                input=user_input,
                previous_response_id=previous_response_id,
                conversation_id=conversation_id,
            )

            # Track whether we streamed/printed text this turn to enable fallback fetch
            streamed_text = False
            printed_message = False
            last_response_id = None

            try:
                async for ev in run.stream_events():
                    if ev.type == "raw_response_event":
                        # Capture conversation id from streaming events
                        etype = getattr(ev.data, "type", None)
                        if etype == "response.created":
                            resp = getattr(ev.data, "response", None)
                            conv = getattr(resp, "conversation", None)
                            cid = getattr(conv, "id", None) if conv is not None else None
                            if cid:
                                conversation_id = cid
                            # capture response id
                            try:
                                last_response_id = getattr(resp, "id", None) or last_response_id
                            except Exception:
                                pass

                        # Optional: debug every event type if env is set
                        import os as _os
                        if _os.environ.get("AGENT_DEBUG_EVENTS"):
                            print(color(f"[{etype}]", "gray"))

                        # Stream assistant output text progressively
                        if etype == "response.output_text.delta":
                            delta = getattr(ev.data, "delta", "") or ""
                            if delta:
                                print(delta, end="", flush=True)
                                streamed_text = True
                                continue
                        elif etype == "response.output_text.done":
                            # Finish the line after streaming
                            if streamed_text:
                                print()
                                printed_message = True
                            continue

                        # Typed events for reasoning / tools / message finalization
                        if isinstance(ev.data, ResponseOutputItemAddedEvent):
                            handle_event_added(ev.data)
                        elif isinstance(ev.data, ResponseOutputItemDoneEvent):
                            item = ev.data.item
                            if item.type == "message":
                                # If we already streamed text, skip duplicate summary print
                                if not streamed_text:
                                    print(color("Assistant:", "blue"), item.content[0].text)
                                    printed_message = True
                            elif item.type == "function_call":
                                name = color(item.name, "magenta")
                                args = item.arguments[1:-1]
                                print(f"{name}({args})")
                        elif isinstance(ev.data, ResponseCompletedEvent):
                            previous_response_id = ev.data.response.id
                            # Reset stream flag for next turn
                            streamed_text = False
                            # capture response id
                            last_response_id = ev.data.response.id or last_response_id

                        # Fallbacks for when typed classes don't match
                        if etype == "response.output_item.added":
                            item = getattr(ev.data, "item", None)
                            if getattr(item, "type", None) == "function_call":
                                name = color(getattr(item, "name", "function"), "magenta")
                                # arguments may still be streaming; print call header
                                print(f"{name}(…) ")
                        elif etype == "response.output_item.done":
                            item = getattr(ev.data, "item", None)
                            itype = getattr(item, "type", None)
                            if itype == "message" and not streamed_text:
                                # Try to extract text from item.content
                                text = ""
                                try:
                                    parts = getattr(item, "content", [])
                                    for p in parts:
                                        if getattr(p, "type", None) == "output_text":
                                            text += getattr(p, "text", "")
                                except Exception:
                                    pass
                                if text:
                                    print(color("Assistant:", "blue"), text)
                                    printed_message = True
                        elif etype == "function.tool_result" and not printed_message:
                            # Synthetic event from core with the tool output
                            name = getattr(ev.data, "name", "tool")
                            result = getattr(ev.data, "result", None)
                            # Friendly one-liner
                            line = synthesize_tool_result_text(name, result)
                            print(color("Assistant:", "blue"), line)
                            # Compact pretty preview for debugging/visibility
                            try:
                                import json as _json
                                pretty = _json.dumps(result, indent=2, ensure_ascii=False)
                            except Exception:
                                pretty = str(result)
                            print(color(f"{name} ->", "magenta"), pretty)
                            printed_message = True
            except (EOFError, KeyboardInterrupt):
                print("\nExiting.")
                break

            # Fallback: if the turn completed without any printed text, fetch the response
            try:
                if not printed_message and last_response_id:
                    client = OpenAI()
                    resp = client.responses.retrieve(response_id=last_response_id)
                    # Extract message text
                    out_text = []
                    for item in resp.output or []:
                        if getattr(item, "type", None) == "message":
                            for part in getattr(item, "content", []) or []:
                                if getattr(part, "type", None) == "output_text":
                                    out_text.append(getattr(part, "text", ""))
                    final = "".join(out_text).strip()
                    if final:
                        print(color("Assistant:", "blue"), final)
                        printed_message = True
            except Exception:
                pass

    try:
        asyncio.run(main())
    except (KeyboardInterrupt, EOFError):
        print("\nExiting.")


def handle_event_added(event: ResponseOutputItemAddedEvent):
    item = event.item
    if item.type == "reasoning":
        print(color("Reasoning...", "gray"), "\n".join(item.summary))


def handle_event_done(event: ResponseOutputItemDoneEvent):
    item = event.item

    if item.type == "message":
        print(color("Assistant:", "blue"), item.content[0].text)
    elif item.type == "function_call":
        name = color(item.name, "magenta")
        args = item.arguments[1:-1]
        print(f"{name}({args})")


def to_dict(obj):
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if dataclasses.is_dataclass(obj):
        return dataclasses.asdict(obj)
    # Prefer real instance __dict__ if it has fields
    d = getattr(obj, "__dict__", None)
    if isinstance(d, dict) and d:
        return d
    # Fallback: build a dict from public attributes (handles synthetic event objects)
    try:
        out = {}
        for name in dir(obj):
            if name.startswith("_"):
                continue
            try:
                val = getattr(obj, name)
            except Exception:
                continue
            if callable(val):
                continue
            out[name] = val
        if out:
            return out
    except Exception:
        pass
    return str(obj)


def encode_sse(ev: str, data: dict) -> bytes:
    return f"event: {ev}\ndata:{json.dumps(data)}\n\n".encode()


async def event_stream(q: asyncio.Queue[bytes], req: Request):
    while True:
        chunk = await q.get()
        yield chunk
        if chunk.startswith(b"event: done") or await req.is_disconnected():
            break


_HALLUCINATE_HISTORY = []


def fn_to_schema(fn) -> Dict[str, Any]:
    """
    Build a minimal function‑tool schema from a python callable.
    All parameters are typed as string for brevity.
    """
    props = {p: {"type": "string"} for p in inspect.signature(fn).parameters}
    docstring = inspect.getdoc(fn) or ""
    return {
        "type": "function",
        "name": fn.__name__,
        "description": docstring,
        "parameters": {
            "type": "object",
            "properties": props,
            "required": list(props),
            "additionalProperties": True,
        },
    }


def _hallucinated_response(
    fn_name: str,
    fn_schema: Dict[str, Any],
    args: Dict[str, Any],
    prev_calls: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Ask the model to fake the function’s output (JSON mode)."""
    client = OpenAI()
    response = client.responses.create(
        model="gpt-4.1-mini",
        input=[
            {
                "role": "developer",
                "content": (
                    "Emulate the function call below. "
                    "You are given the function's JSON schema, the arguments, "
                    "and a history of prior calls. Respond with a JSON object "
                    "representing the function's return value."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "function_schema": fn_schema,
                        "function_name": fn_name,
                        "args": args,
                        "previous_function_calls": prev_calls,
                    },
                    indent=2,
                ),
            },
        ],
        text={"format": {"type": "json_object"}},
        reasoning={"effort": "low"},
    )

    output_text = "".join(
        part.text
        for item in response.output
        if item.type == "message"
        for part in item.content
        if part.type == "output_text"
    )
    return json.loads(output_text or "{}")


def hallucinate(fn: Callable) -> Callable:
    """
    Decorator that swaps the real implementation for an LLM‑generated one.
    Uses a **single, module‑level history list** shared by all hallucinated fns.
    """
    schema = fn_to_schema(fn)  # build once

    @wraps(fn)
    def wrapper(*args, **kwargs):
        arg_names = fn.__code__.co_varnames[: fn.__code__.co_argcount]
        arg_dict = {**dict(zip(arg_names, args)), **kwargs}

        result = _hallucinated_response(
            fn.__name__, schema, arg_dict, _HALLUCINATE_HISTORY
        )

        _HALLUCINATE_HISTORY.append(
            {
                "name": fn.__name__,
                "schema": schema,
                "args": arg_dict,
                "returned": result,
            }
        )
        return result

    return wrapper
