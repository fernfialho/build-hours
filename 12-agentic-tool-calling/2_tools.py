import os
from agents import Agent, function_tool as tool
from utils import run_demo_loop
from mock_api import MockAPI
from typing import Optional

mock_api = MockAPI()


@tool
def search_policies(query: str):
    return mock_api.search_policies(query)


@tool
def get_emails(to: Optional[str] = None):
    return mock_api.get_emails(to)


@tool
def send_email(from_addr: str, to_addr: str, subject: str, body: str):
    return mock_api.send_email(from_addr, to_addr, subject, body)


MODEL = os.getenv("AGENT_MODEL", "o3")

agent = Agent(
    name="Assistant",
    model=MODEL,
    instructions=(
        "You can call tools: search_policies, get_emails, send_email. "
        "After any tool call, ALWAYS produce a short, direct final message that answers the user. "
        "Do not end with only a tool call."
    ),
    tools=[search_policies, get_emails, send_email],
)

run_demo_loop(agent)
