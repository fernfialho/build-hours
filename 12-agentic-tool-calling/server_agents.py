import os
from agents import Agent, function_tool
from agents.model_settings import ModelSettings
from openai.types.shared.reasoning import Reasoning
from typing import List, Dict, Optional, Any
from mock_api import MockAPI


mock_api = MockAPI()


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
    """Search open tickets by query string."""
    return mock_api.search_open_tickets(query)


@function_tool
def read_document(doc_id: int) -> Optional[Dict[str, Any]]:
    """Read a document (runbook) by its ID."""
    return mock_api.read_document(doc_id)


@function_tool
def get_runbook_by_category(category: str) -> Optional[Dict[str, Any]]:
    """Get a runbook document by category."""
    return mock_api.get_runbook_by_category(category)


@function_tool
def search_policies(query: str) -> List[Dict[str, Any]]:
    """Search policies by query string."""
    return mock_api.search_policies(query)


@function_tool
def get_emails(to: Optional[str] = None) -> List[Dict[str, Any]]:
    """Get emails, optionally filtered by recipient."""
    return mock_api.get_emails(to)


@function_tool
def add_ticket_comment(ticket_id: int, comment: str) -> Optional[List[str]]:
    """Add a comment to a ticket."""
    return mock_api.add_ticket_comment(ticket_id, comment)


@function_tool
def write_document(
    title: str, content: str, doc_id: Optional[int] = None
) -> Dict[str, Any]:
    """Create or update a document."""
    return mock_api.write_document(title, content, doc_id)


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
        "Begin by planning with concise TODOs, then execute them, checking off as you go. "
        "When tools help, call them; after tool use, produce a brief, direct answer. "
        "Operate autonomously to reach a conclusion."
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
    ],
)
