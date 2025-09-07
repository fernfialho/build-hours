from __future__ import annotations

import asyncio
import inspect
import json
import threading
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Dict, Generic, Iterable, List, Optional, TypeVar

# Ensure secrets are loaded into environment for OpenAI()
try:  # noqa: F401
    import bootstrap_secrets  # type: ignore
except Exception:
    pass


T = TypeVar("T")


class RunContextWrapper(Generic[T]):
    """Simple wrapper passed to function tools to provide context access."""

    def __init__(self, context: T):
        self.context = context


@dataclass
class Agent:
    """Minimal Agent container used by the demos.

    Attributes
    - name: display name of the agent
    - model: model identifier string
    - instructions: optional system instructions
    - tools: list of callable tools (sync or async)
    - model_settings: optional configuration object
    """

    name: str
    model: str
    instructions: str | None = None
    tools: List[Callable[..., Any]] | None = None
    model_settings: Any | None = None
    # Note: conversation is passed per-run, do not persist on Agent

    def __post_init__(self):
        if self.tools is None:
            self.tools = []


def function_tool(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator to mark a callable as a tool.

    This tags the function for discovery and returns it unchanged.
    """

    setattr(fn, "__is_tool__", True)
    return fn


@dataclass
class RawResponseEvent:
    """Envelope that matches the repo's expectation for streaming events.

    type: always "raw_response_event"
    data: the OpenAI response stream event object
    """

    type: str
    data: Any


def _build_function_tools(tools: Iterable[Callable[..., Any]]) -> List[Dict[str, Any]]:
    """Convert Python callables into Responses API function tool schemas.

    Shape expected by Responses API (not Chat Completions):
    {"type":"function", "name": str, "parameters": {...}, "description": str?, "strict": bool?}
    Marks only truly required parameters (no defaults) as required.
    """
    out: List[Dict[str, Any]] = []
    for fn in tools or []:
        if not callable(fn):
            continue
        try:
            sig = inspect.signature(fn)
            props: Dict[str, Any] = {}
            required: List[str] = []
            for name, p in sig.parameters.items():
                if name == "wrapper":  # our runtime injects this
                    continue
                props[name] = {"type": "string"}
                if p.default is inspect._empty and p.kind in (
                    inspect.Parameter.POSITIONAL_ONLY,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    inspect.Parameter.KEYWORD_ONLY,
                ):
                    required.append(name)
        except (TypeError, ValueError):
            props = {}
            required = []

        schema = {
            "type": "function",
            "name": fn.__name__,
            "description": (inspect.getdoc(fn) or "").strip() or None,
            "parameters": {
                "type": "object",
                "properties": props,
                "required": required,
                "additionalProperties": True,
            },
            "strict": False,
        }
        out.append(schema)
    return out


class _Run:
    """Represents a streaming run backed by OpenAI Responses API."""

    def __init__(
        self,
        agent: Agent,
        input: Any,
        previous_response_id: Optional[str] = None,
        context: Any = None,
        max_turns: int = 1,
        conversation_id: Optional[str] = None,
    ) -> None:
        self.agent = agent
        self.input = input
        self.previous_response_id = previous_response_id
        self.context = context
        self.max_turns = max_turns
        self.conversation_id: Optional[str] = conversation_id

    async def stream_events(self) -> AsyncIterator[RawResponseEvent]:
        """Stream with tool execution: run local tools and chain outputs back.

        - Streams events from OpenAI Responses API
        - Detects function-call events and executes matching Python tools
        - Submits tool outputs via a chained `responses.stream` call using
          `previous_response_id`
        """
        from openai import OpenAI

        q: asyncio.Queue[RawResponseEvent] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def to_jsonable(obj: Any) -> Any:
            try:
                return json.loads(json.dumps(obj))  # fast path if already jsonable
            except Exception:
                pass
            if hasattr(obj, "model_dump"):
                return obj.model_dump()
            try:
                import dataclasses as _dc

                if _dc.is_dataclass(obj):
                    return _dc.asdict(obj)
            except Exception:
                pass
            return getattr(obj, "__dict__", str(obj))

        def call_tool(fn: Callable[..., Any], args: Dict[str, Any]) -> Any:
            sig = None
            try:
                sig = inspect.signature(fn)
            except Exception:
                pass

            positional = []
            if sig is not None and len(sig.parameters) > 0:
                first = next(iter(sig.parameters.values()))
                if first.name in {"wrapper", "context"} or (
                    first.annotation and "RunContextWrapper" in str(first.annotation)
                ):
                    positional.append(RunContextWrapper(self.context))

            if inspect.iscoroutinefunction(fn):
                fut = asyncio.run_coroutine_threadsafe(
                    fn(*positional, **args), loop
                )
                return fut.result()
            else:
                return fn(*positional, **args)

        def run_stream(
            initial: bool,
            *,
            prev_id: Optional[str],
            tool_output: Optional[Dict[str, Any]],
            ignore_prev: bool = False,
        ):
            client = OpenAI()

            # Build request for either initial create or chained create
            req: Dict[str, Any] = {
                "model": self.agent.model,
            }
            # Prefer Conversations over previous_response_id for continuity
            if self.conversation_id:
                req["conversation"] = self.conversation_id
            if initial:
                req["input"] = self.input
                if self.agent.instructions:
                    req["instructions"] = self.agent.instructions
                # When using conversations, do not send previous_response_id
                if (not self.conversation_id) and self.previous_response_id and not ignore_prev:
                    req["previous_response_id"] = self.previous_response_id
                # Ensure response is persisted so we can chain tool outputs
                req["store"] = True
            else:
                # Chained call: must reference the specific prior response
                # Always provide previous_response_id so the function_call_output
                # attaches to the correct pending function call.
                if prev_id:
                    req["previous_response_id"] = prev_id
                # Optionally include conversation for continuity, but do not rely on it
                if self.conversation_id:
                    req["conversation"] = self.conversation_id
                # Responses API expects a string or an array of input items
                # for chaining tool outputs we send an array with one
                # function_call_output item.
                req["input"] = [tool_output]  # type: ignore[assignment]
                req["store"] = True

            # Optional reasoning settings
            reasoning = getattr(getattr(self.agent, "model_settings", None), "reasoning", None)
            if reasoning is not None:
                req["reasoning"] = reasoning

            # Tools (function calling)
            tools_param = _build_function_tools(self.agent.tools or [])
            if tools_param:
                req["tools"] = tools_param

            # Map of function_call item_id -> {name, call_id, args}
            func_calls: Dict[str, Dict[str, Any]] = {}

            with client.responses.stream(**req) as stream:
                current_response_id = None

                for event in stream:
                    # Always forward raw event to async consumer
                    loop.call_soon_threadsafe(
                        q.put_nowait, RawResponseEvent("raw_response_event", event)
                    )

                    etype = getattr(event, "type", None)

                    # Capture response id as soon as it appears on any event
                    if current_response_id is None:
                        rid = getattr(event, "response", None)
                        if rid is not None:
                            current_response_id = getattr(rid, "id", None)

                    # Track response id early on create events
                    if etype == "response.created":
                        rid = getattr(event, "response", None)
                        if rid is not None:
                            current_response_id = getattr(rid, "id", None)
                            # Capture conversation id and persist on agent
                            conv = getattr(rid, "conversation", None)
                            cid = getattr(conv, "id", None) if conv is not None else None
                            if cid:
                                self.conversation_id = cid

                    # Track function_call item when added
                    if etype == "response.output_item.added":
                        item = getattr(event, "item", None)
                        if getattr(item, "type", None) == "function_call":
                            item_id = getattr(item, "id", None)
                            if item_id:
                                func_calls[item_id] = {
                                    "name": getattr(item, "name", None),
                                    "call_id": getattr(item, "call_id", None),
                                    "args": "",
                                }

                    # Accumulate arguments deltas
                    elif etype == "response.function_call_arguments.delta":
                        item_id = getattr(event, "item_id", None)
                        delta = getattr(event, "delta", "") or ""
                        if item_id and item_id in func_calls:
                            func_calls[item_id]["args"] += delta

                    # On arguments.done: run tool and chain a new stream
                    elif etype == "response.function_call_arguments.done":
                        item_id = getattr(event, "item_id", None)
                        info = func_calls.get(item_id or "")
                        if info and info.get("name") and info.get("call_id"):
                            name = info["name"]
                            call_id = info["call_id"]
                            args_json = info["args"] or getattr(event, "arguments", "{}")
                            try:
                                args = json.loads(args_json or "{}")
                            except Exception:
                                args = {}

                            # Find matching tool by name
                            fn = None
                            for tfn in self.agent.tools or []:
                                if getattr(tfn, "__name__", None) == name:
                                    fn = tfn
                                    break

                            if fn is not None:
                                try:
                                    result = call_tool(fn, args or {})
                                except Exception as tool_err:
                                    result = {"error": str(tool_err)}

                                # Emit a synthetic event with the tool result so UIs
                                # can surface something even if the model doesn't follow up
                                loop.call_soon_threadsafe(
                                    q.put_nowait,
                                    RawResponseEvent(
                                        "raw_response_event",
                                        type(
                                            "E",
                                            (),
                                            {
                                                "type": "function.tool_result",
                                                "name": name,
                                                "result": result,
                                            },
                                        )(),
                                    ),
                                )

                                output_item = {
                                    "type": "function_call_output",
                                    "call_id": call_id,
                                    "output": json.dumps(to_jsonable(result)),
                                }

                                # Chain next stream with tool output (requires previous_response_id)
                                return (
                                    "chain",
                                    {
                                        "previous_response_id": current_response_id,
                                        "input": output_item,
                                    },
                                )

                    # If response completed, stop streaming
                    elif etype == "response.completed":
                        return ("done", None)

            return ("closed", None)

        # Producer loop: stream, possibly chain new streams when tools are called
        def producer() -> None:
            try:
                mode = "initial"
                prev = None
                payload = None
                ignore_prev = False
                chain_retries = 0
                from openai import BadRequestError
                while True:
                    try:
                        if mode == "initial":
                            action, data = run_stream(
                                True, prev_id=None, tool_output=None, ignore_prev=ignore_prev
                            )
                        elif mode == "chain":
                            action, data = run_stream(
                                False, prev_id=prev, tool_output=payload
                            )
                        else:
                            break
                    except BadRequestError as e:
                        msg = str(e).lower()
                        if mode == "initial" and (
                            "previous response" in msg or "previous_response_id" in msg
                        ):
                            ignore_prev = True
                            mode = "initial"
                            continue
                        # Occasionally, chaining by previous_response_id can race with store.
                        # Retry a few times before giving up.
                        if mode == "chain" and (
                            "previous response" in msg
                            or "previous_response_id" in msg
                            or "not found" in msg
                        ):
                            if chain_retries < 3:
                                chain_retries += 1
                                import time as _t

                                _t.sleep(0.25 * chain_retries)
                                mode = "chain"
                                continue
                            else:
                                # Give up chaining; close the stream gracefully
                                break
                        else:
                            raise

                    # Process action from run_stream
                    if action == "chain":
                        prev = data["previous_response_id"]
                        payload = data["input"]
                        mode = "chain"
                        chain_retries = 0
                        continue
                    elif action in {"done", "closed"}:
                        break
                    else:
                        break
            finally:
                loop.call_soon_threadsafe(
                    q.put_nowait,
                    RawResponseEvent("raw_response_event", type("E", (), {"type": "response.closed"})()),
                )

        t = threading.Thread(target=producer, daemon=True)
        t.start()

        while True:
            ev = await q.get()
            yield ev
            etype = getattr(ev.data, "type", None)
            if etype in {"response.completed", "response.closed"}:
                break


class Runner:
    """Factory for creating streamed runs using OpenAI Responses API."""

    @staticmethod
    def run_streamed(
        agent: Agent,
        input: Any,
        previous_response_id: Optional[str] = None,
        context: Any = None,
        max_turns: int = 1,
        conversation_id: Optional[str] = None,
    ) -> _Run:
        return _Run(
            agent=agent,
            input=input,
            previous_response_id=previous_response_id,
            context=context,
            max_turns=max_turns,
            conversation_id=conversation_id,
        )
