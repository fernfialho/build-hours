"""
Lightweight stubs for an `agents` package used by the demos.

This provides minimal interfaces so the examples can import and run
without the original dependency. It does not implement full LLM
tool-calling behavior; `Runner.run_streamed` yields no events.
"""

from .core import Agent, Runner, function_tool, RunContextWrapper
from .model_settings import ModelSettings

__all__ = [
    "Agent",
    "Runner",
    "function_tool",
    "RunContextWrapper",
    "ModelSettings",
]

