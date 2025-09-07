from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class ModelSettings:
    """Container for optional model configuration.

    A minimal stand-in so `from agents.model_settings import ModelSettings`
    resolves. Accepts arbitrary values (e.g., Reasoning objects).
    """

    reasoning: Optional[Any] = None

