import os
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from agents import Agent, function_tool
from agents.model_settings import ModelSettings
from openai.types.shared.reasoning import Reasoning
from typing import List, Dict, Optional, Any, Iterable
from mock_api import MockAPI


mock_api = MockAPI()


def _keywords(q: str, limit: int = 3) -> List[str]:
    """Extract up to N salient keywords from a natural-language query."""
    q = (q or "").lower()
    # remove punctuation
    q = re.sub(r"[^a-z0-9\s]", " ", q)
    toks = [t for t in q.split() if t and len(t) > 2]
    stop = {
        "the","a","an","and","or","but","about","with","for","of","on","in","to","from",
        "please","show","find","search","open","ticket","tickets","policy","policies","document",
        "runbook","summarize","add","short","comment","list","subjects","recent","emails","email",
    }
    toks = [t for t in toks if t not in stop]
    # choose longest N unique tokens
    uniq = []
    for t in sorted(toks, key=lambda x: (-len(x), x)):
        if t not in uniq:
            uniq.append(t)
        if len(uniq) >= limit:
            break
    return uniq or (toks[:1] if toks else [])


def _dedupe(items: Iterable[Dict[str, Any]], key: str = "id") -> List[Dict[str, Any]]:
    seen = set()
    out: List[Dict[str, Any]] = []
    for it in items:
        k = it.get(key)
        if k in seen:
            continue
        seen.add(k)
        out.append(it)
    return out


def _infer_tz(location: Optional[str]) -> Optional[str]:
    if not location:
        return None
    s = (location or "").strip().lower()
    if not s:
        return None
    # Normalize punctuation to spaces for robust matching (e.g., "providence, ri")
    import re as _re
    s_norm = _re.sub(r"[^a-z0-9]+", " ", s).strip()
    if "utc" in s_norm:
        return "UTC"
    # lightweight mapping of frequent locations/regions
    mapping = {
        # US East
        "rhode island": "America/New_York",
        "ri": "America/New_York",
        "providence": "America/New_York",
        "new york": "America/New_York",
        "ny": "America/New_York",
        "boston": "America/New_York",
        "massachusetts": "America/New_York",
        "ma": "America/New_York",
        "connecticut": "America/New_York",
        "ct": "America/New_York",
        "new jersey": "America/New_York",
        "nj": "America/New_York",
        "washington dc": "America/New_York",
        "dc": "America/New_York",
        # US Central
        "chicago": "America/Chicago",
        "illinois": "America/Chicago",
        "il": "America/Chicago",
        # US Mountain
        "denver": "America/Denver",
        "colorado": "America/Denver",
        "co": "America/Denver",
        # US Pacific
        "san francisco": "America/Los_Angeles",
        "sf": "America/Los_Angeles",
        "los angeles": "America/Los_Angeles",
        "la": "America/Los_Angeles",
        "california": "America/Los_Angeles",
        "ca": "America/Los_Angeles",
        "seattle": "America/Los_Angeles",
        "washington": "America/Los_Angeles",
        "wa": "America/Los_Angeles",
        # EU/UK
        "london": "Europe/London",
        "uk": "Europe/London",
        "gb": "Europe/London",
        "paris": "Europe/Paris",
        "france": "Europe/Paris",
        "berlin": "Europe/Berlin",
        "germany": "Europe/Berlin",
        # APAC
        "tokyo": "Asia/Tokyo",
        "japan": "Asia/Tokyo",
        "sydney": "Australia/Sydney",
        "australia": "Australia/Sydney",
        "singapore": "Asia/Singapore",
    }
    # Exact canonical match on normalized string
    if s_norm in mapping:
        return mapping[s_norm]
    # Substring match on word boundaries for multi-token inputs
    for key, tz in sorted(mapping.items(), key=lambda kv: -len(kv[0])):
        if _re.search(rf"\b{_re.escape(key)}\b", s_norm):
            return tz
    # simple heuristic: recognize continent/city strings directly
    if "/" in s:
        try:
            ZoneInfo(s)
            return s
        except Exception:
            return None
    return None


@function_tool
def get_time(location: Optional[str] = None) -> Dict[str, Any]:
    """Get the current time. If a location is provided (city/state/country or IANA zone), return local time there."""
    now_utc = datetime.now(timezone.utc)
    tz_name = _infer_tz(location) or "UTC"
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("UTC")
        tz_name = "UTC"
    local = now_utc.astimezone(tz)

    # Cross-platform safe strftime (avoid %-d on Windows); keep readable format
    formatted = local.strftime("%I:%M %p on %d %B %Y").lstrip("0")
    return {
        "iso": local.isoformat(),
        "tz": tz_name,
        "abbr": local.tzname(),
        "offset_minutes": int((local.utcoffset() or timezone.utc.utcoffset(local)).total_seconds() // 60),
        "formatted": formatted,
        "location_resolved": location or tz_name,
    }


@function_tool
def get_weather(city: str) -> dict:
    """Get the current weather for a given city."""
    return {
        "city": city,
        "temperature": "22 °C",
        "condition": "Sunny",
        "humidity": "40 %",
        "wind": "10 km/h",
    }


@function_tool
def search_open_tickets(query: str) -> List[Dict[str, Any]]:
    """Search open tickets by query. Falls back to top keywords when the query is long."""
    # First try direct (model may already be concise)
    results = mock_api.search_open_tickets(query)
    if results:
        return results
    # Fallback: try 1–3 keywords individually and union
    out: List[Dict[str, Any]] = []
    for k in _keywords(query, limit=3):
        out.extend(mock_api.search_open_tickets(k))
    return _dedupe(out)


@function_tool
def read_document(doc_id: Any) -> Optional[Dict[str, Any]]:
    """Read a document (runbook) by its ID (accepts string IDs)."""
    try:
        did = int(str(doc_id).strip())
    except Exception:
        did = -1
    return mock_api.read_document(did)


@function_tool
def get_runbook_by_category(category: str) -> Optional[Dict[str, Any]]:
    """Get a runbook document by category."""
    return mock_api.get_runbook_by_category(category)


@function_tool
def search_policies(query: str) -> List[Dict[str, Any]]:
    """Search policies by query. Tries concise keywords if the full query misses."""
    results = mock_api.search_policies(query)
    if results:
        return results
    out: List[Dict[str, Any]] = []
    for k in _keywords(query, limit=3):
        out.extend(mock_api.search_policies(k))
    return _dedupe(out)


@function_tool
def get_emails(to: Optional[str] = None) -> List[Dict[str, Any]]:
    """Get emails, optionally filtered by recipient."""
    return mock_api.get_emails(to)


@function_tool
def add_ticket_comment(ticket_id: Any, comment: str) -> Optional[List[str]]:
    """Add a comment to a ticket (accepts string IDs)."""
    try:
        tid = int(str(ticket_id).strip())
    except Exception:
        tid = -1
    return mock_api.add_ticket_comment(tid, comment)


@function_tool
def write_document(
    title: str, content: str, doc_id: Optional[Any] = None
) -> Dict[str, Any]:
    """Create or update a document (accepts string IDs)."""
    did: Optional[int] = None
    if doc_id is not None:
        try:
            did = int(str(doc_id).strip())
        except Exception:
            did = None
    return mock_api.write_document(title, content, did)


@function_tool
def send_email(from_addr: str, to_addr: str, subject: str, body: str) -> Dict[str, Any]:
    """Send an email."""
    return mock_api.send_email(from_addr, to_addr, subject, body)


MODEL = os.getenv("AGENT_MODEL", "o3")
# o3 requires reasoning.summary="detailed"; use concise for others
reasoning_summary = "detailed" if MODEL.startswith("o3") else "concise"

agent = Agent(
    name="assistant",
    instructions=(
        "You can and should call tools to gather facts and take actions. "
        "Use get_time for current time queries; if a location is provided, convert to its local time. "
        "When searching, use concise 1–3 keyword queries; if a search returns nothing, retry with shorter keywords. "
        "When using IDs (e.g., tickets/documents), ensure they are integers. "
        "Begin by planning with concise TODOs, then execute them, checking off as you go. "
        "After tool use, produce a brief, direct answer. Operate autonomously to reach a conclusion."
    ),
    model=MODEL,
    model_settings=ModelSettings(reasoning=Reasoning(summary=reasoning_summary)),
    tools=[
        get_weather,
        search_open_tickets,
        read_document,
        get_runbook_by_category,
        search_policies,
        get_emails,
        add_ticket_comment,
        write_document,
        send_email,
        get_time,
    ],
)
