import os
from dataclasses import dataclass
from typing import Any, Callable, Optional

from langchain.agents import create_agent
from langchain.messages import AIMessage, HumanMessage, ToolMessage
from langchain_openai import ChatOpenAI

from libsentrykube.prompts import SYSTEM_PROMPT, USER_PROMPT
from libsentrykube.tools import build_tools

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "anthropic/claude-sonnet-4.5"

# Tool results can be whole rendered manifests. Only show the head of one.
MAX_REPORTED_RESULT = 400


class AgentConfigurationError(Exception):
    """The agent is missing the configuration it needs to talk to the model."""


@dataclass(frozen=True)
class AgentConfig:
    api_key: str
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL


def load_agent_config() -> AgentConfig:
    """
    Builds the agent configuration from the environment.

    OPENROUTER_API_KEY is required. OPENROUTER_BASE_URL is optional and defaults
    to the public OpenRouter endpoint.
    """
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise AgentConfigurationError(
            "OPENROUTER_API_KEY is not set. Set it to your OpenRouter API key "
            "(and optionally OPENROUTER_BASE_URL) to use the agent."
        )

    return AgentConfig(
        api_key=api_key,
        base_url=os.environ.get("OPENROUTER_BASE_URL") or DEFAULT_BASE_URL,
    )


@dataclass(frozen=True)
class AgentStep:
    """
    Something the agent did, reported as it happens.

    `kind` is one of:
      - "thought": the agent reasoned about what to do next.
      - "tool_call": the agent decided to call a tool. `tool` and `tool_input`
        are set.
      - "tool_result": a tool returned. `tool` and `detail` are set.
    """

    kind: str
    message: str
    tool: Optional[str] = None
    tool_input: Optional[dict] = None
    detail: Optional[str] = None


# Called once per step while the agent works. See `AgentStep`.
StepCallback = Callable[[AgentStep], None]


def run_agent(
    query: str,
    region: str,
    cluster: Optional[str] = None,
    config: Optional[AgentConfig] = None,
    on_step: Optional[StepCallback] = None,
) -> str:
    """
    Runs `query` through a langchain agent and returns the agent's response.

    The prompts live in `libsentrykube.prompts`. `region` and `cluster` describe
    what the operator is working on and are rendered into the user prompt.
    `config` defaults to being read from the environment.

    If `on_step` is given it is called with an `AgentStep` every time the agent
    reasons or uses a tool, so callers can show progress while it works.
    """
    if config is None:
        config = load_agent_config()

    model = ChatOpenAI(
        model=config.model,
        base_url=config.base_url,
        api_key=config.api_key,
    )

    agent = create_agent(
        model=model,
        tools=build_tools(region, cluster),
        system_prompt=SYSTEM_PROMPT,
    )

    message = HumanMessage(
        USER_PROMPT.format(query=query, region=region, cluster=cluster or "")
    )
    payload = {"messages": [message]}

    if on_step is None:
        result = agent.invoke(payload)
        return _message_text(result["messages"][-1])

    # Streaming so we can report each step. The last message the model produces
    # is the answer, so hold on to it as we go.
    answer = ""
    for update in agent.stream(payload, stream_mode="updates"):
        for node_update in update.values():
            if not isinstance(node_update, dict):
                continue
            for message in node_update.get("messages", []):
                text = _message_text(message)
                for step in _steps_for(message, text):
                    on_step(step)
                if isinstance(message, AIMessage) and text:
                    answer = text

    return answer


def _steps_for(message: Any, text: str) -> list[AgentStep]:
    """
    Turns one streamed message into the steps worth reporting.

    A single model message can both reason and call tools, so this can return
    more than one step.
    """
    steps = []

    if isinstance(message, ToolMessage):
        result = text.strip()
        # The line count is of the whole result, so it stays honest even
        # though `detail` only carries the head of it.
        lines = len(result.splitlines())
        detail = result
        if len(detail) > MAX_REPORTED_RESULT:
            detail = detail[:MAX_REPORTED_RESULT] + "..."
        return [
            AgentStep(
                kind="tool_result",
                message=f"{message.name} returned {lines} line(s)",
                tool=message.name,
                detail=detail,
            )
        ]

    if isinstance(message, AIMessage):
        if text.strip():
            steps.append(AgentStep(kind="thought", message=text.strip()))
        for call in message.tool_calls or []:
            args = call.get("args") or {}
            steps.append(
                AgentStep(
                    kind="tool_call",
                    message=f"Calling {call['name']}",
                    tool=call["name"],
                    tool_input=args,
                )
            )

    return steps


def _message_text(message: Any) -> str:
    """
    Extracts the plain text out of a message.

    `content` is usually a string, but it can also be a list of content blocks,
    in which case we concatenate the text ones and drop the rest.
    """
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "".join(parts)

    return str(content)
