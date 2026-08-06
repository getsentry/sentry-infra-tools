import os
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from langchain.agents import create_agent
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI


DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "anthropic/claude-sonnet-4.5"


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


def run_agent(
    query: str,
    system_prompt: Optional[str] = None,
    tools: Optional[Sequence[BaseTool]] = None,
    config: Optional[AgentConfig] = None,
) -> str:
    """
    Runs `query` through a langchain agent and returns the agent's response.

    `system_prompt` and `tools` are optional so callers can shape the agent
    without this module knowing about them. `config` defaults to being read from
    the environment.
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
        tools=list(tools) if tools else [],
        system_prompt=system_prompt,
    )

    result = agent.invoke({"messages": [{"role": "user", "content": query}]})

    return _message_text(result["messages"][-1])


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
