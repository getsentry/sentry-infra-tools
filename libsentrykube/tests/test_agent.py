from unittest.mock import MagicMock, patch

import pytest

from libsentrykube.agent import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    TOOLS,
    AgentConfig,
    AgentConfigurationError,
    load_agent_config,
    run_agent,
)
from libsentrykube.prompts import SYSTEM_PROMPT, USER_PROMPT


def test_load_agent_config_defaults(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "TEST_KEY")
    monkeypatch.delenv("OPENROUTER_BASE_URL", raising=False)

    config = load_agent_config()

    assert config == AgentConfig(
        api_key="TEST_KEY", base_url=DEFAULT_BASE_URL, model=DEFAULT_MODEL
    )


def test_load_agent_config_custom_base_url(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "TEST_KEY")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://example.com/v1")

    assert load_agent_config().base_url == "https://example.com/v1"


def test_load_agent_config_missing_key(monkeypatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    with pytest.raises(AgentConfigurationError, match="OPENROUTER_API_KEY"):
        load_agent_config()


def _mock_agent(content) -> MagicMock:
    agent = MagicMock()
    agent.invoke.return_value = {"messages": [MagicMock(content=content)]}
    return agent


def test_run_agent() -> None:
    config = AgentConfig(api_key="TEST_KEY", base_url="https://example.com/v1")

    with (
        patch("libsentrykube.agent.ChatOpenAI") as mock_model,
        patch("libsentrykube.agent.create_agent") as mock_create_agent,
    ):
        mock_create_agent.return_value = _mock_agent("42")

        response = run_agent(
            "what is 6 times 7?",
            region="us",
            cluster="default",
            config=config,
        )

    assert response == "42"

    mock_model.assert_called_once_with(
        model=DEFAULT_MODEL,
        base_url="https://example.com/v1",
        api_key="TEST_KEY",
    )
    mock_create_agent.assert_called_once_with(
        model=mock_model.return_value,
        tools=TOOLS,
        system_prompt=SYSTEM_PROMPT,
    )
    mock_create_agent.return_value.invoke.assert_called_once_with(
        {
            "messages": [
                {
                    "role": "user",
                    "content": USER_PROMPT.format(
                        query="what is 6 times 7?", region="us", cluster="default"
                    ),
                }
            ]
        }
    )


def test_run_agent_query_region_and_cluster_reach_the_prompt() -> None:
    """Whatever the prompt looks like, the three values must be rendered into it."""
    with (
        patch("libsentrykube.agent.ChatOpenAI"),
        patch("libsentrykube.agent.create_agent") as mock_create_agent,
    ):
        mock_create_agent.return_value = _mock_agent("ok")

        run_agent(
            "why is it down?",
            region="s4s",
            cluster="primary",
            config=AgentConfig(api_key="TEST_KEY"),
        )

    content = mock_create_agent.return_value.invoke.call_args[0][0]["messages"][0][
        "content"
    ]
    assert "why is it down?" in content
    assert "s4s" in content
    assert "primary" in content


def test_run_agent_without_cluster() -> None:
    """cluster is optional and must not leak a `None` into the prompt."""
    with (
        patch("libsentrykube.agent.ChatOpenAI"),
        patch("libsentrykube.agent.create_agent") as mock_create_agent,
    ):
        mock_create_agent.return_value = _mock_agent("ok")

        run_agent("hi", region="us", config=AgentConfig(api_key="TEST_KEY"))

    content = mock_create_agent.return_value.invoke.call_args[0][0]["messages"][0][
        "content"
    ]
    assert "None" not in content


def test_run_agent_loads_config_from_env(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "ENV_KEY")
    monkeypatch.delenv("OPENROUTER_BASE_URL", raising=False)

    with (
        patch("libsentrykube.agent.ChatOpenAI") as mock_model,
        patch("libsentrykube.agent.create_agent") as mock_create_agent,
    ):
        mock_create_agent.return_value = _mock_agent("hello")

        assert run_agent("hi", region="us") == "hello"

    mock_model.assert_called_once_with(
        model=DEFAULT_MODEL, base_url=DEFAULT_BASE_URL, api_key="ENV_KEY"
    )
    mock_create_agent.assert_called_once_with(
        model=mock_model.return_value, tools=TOOLS, system_prompt=SYSTEM_PROMPT
    )


def test_run_agent_content_blocks() -> None:
    """The model can return a list of content blocks instead of a plain string."""
    content = [
        {"type": "text", "text": "hello "},
        {"type": "thinking", "thinking": "dropped"},
        {"type": "text", "text": "world"},
    ]

    with (
        patch("libsentrykube.agent.ChatOpenAI"),
        patch("libsentrykube.agent.create_agent") as mock_create_agent,
    ):
        mock_create_agent.return_value = _mock_agent(content)

        response = run_agent("hi", region="us", config=AgentConfig(api_key="TEST_KEY"))

    assert response == "hello world"
