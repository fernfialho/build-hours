"""
Bootstrap environment from `secrets_config.py` if present.

Import this module early to ensure `OPENAI_API_KEY` is available for
`openai.OpenAI()` without having to pass it explicitly.
"""

import os

try:
    import secrets_config as _secrets  # type: ignore

    api_key = getattr(_secrets, "OPENAI_API_KEY", None)
    if api_key:
        os.environ.setdefault("OPENAI_API_KEY", api_key)
except Exception:
    # Fail silently if secrets file is missing or malformed
    pass

