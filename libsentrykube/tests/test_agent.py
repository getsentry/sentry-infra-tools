from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from libsentrykube.agent import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    AgentConfig,
    AgentConfigurationError,
    AgentStep,
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
        patch("libsentrykube.agent.build_tools") as mock_build_tools,
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
    mock_build_tools.assert_called_once_with("us", "default")
    mock_create_agent.assert_called_once_with(
        model=mock_model.return_value,
        tools=mock_build_tools.return_value,
        system_prompt=SYSTEM_PROMPT,
    )
    mock_create_agent.return_value.invoke.assert_called_once_with(
        {
            "messages": [
                {
                    "role": "user",
                    "content": USER_PROMPT.format(query="what is 6 times 7?"),
                }
            ]
        }
    )


def test_run_agent_query_reaches_the_prompt() -> None:
    """Whatever the prompt looks like, the query must be rendered into it."""
    with (
        patch("libsentrykube.agent.ChatOpenAI"),
        patch("libsentrykube.agent.create_agent") as mock_create_agent,
        patch("libsentrykube.agent.build_tools"),
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


def test_run_agent_region_and_cluster_reach_the_tools() -> None:
    """
    Region and cluster are not in the prompt. They are bound to the tools so
    the model cannot reach into a different region.
    """
    with (
        patch("libsentrykube.agent.ChatOpenAI"),
        patch("libsentrykube.agent.create_agent") as mock_create_agent,
        patch("libsentrykube.agent.build_tools") as mock_build_tools,
    ):
        mock_create_agent.return_value = _mock_agent("ok")

        run_agent(
            "why is it down?",
            region="s4s",
            cluster="primary",
            config=AgentConfig(api_key="TEST_KEY"),
        )

    mock_build_tools.assert_called_once_with("s4s", "primary")


def test_run_agent_loads_config_from_env(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "ENV_KEY")
    monkeypatch.delenv("OPENROUTER_BASE_URL", raising=False)

    with (
        patch("libsentrykube.agent.ChatOpenAI") as mock_model,
        patch("libsentrykube.agent.create_agent") as mock_create_agent,
        patch("libsentrykube.agent.build_tools") as mock_build_tools,
    ):
        mock_create_agent.return_value = _mock_agent("hello")

        assert run_agent("hi", region="us") == "hello"

    mock_model.assert_called_once_with(
        model=DEFAULT_MODEL, base_url=DEFAULT_BASE_URL, api_key="ENV_KEY"
    )
    mock_build_tools.assert_called_once_with("us", None)
    mock_create_agent.assert_called_once_with(
        model=mock_model.return_value,
        tools=mock_build_tools.return_value,
        system_prompt=SYSTEM_PROMPT,
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
        patch("libsentrykube.agent.build_tools"),
    ):
        mock_create_agent.return_value = _mock_agent(content)

        response = run_agent("hi", region="us", config=AgentConfig(api_key="TEST_KEY"))

    assert response == "hello world"


def _streaming_agent(updates) -> MagicMock:
    agent = MagicMock()
    agent.stream.return_value = iter(updates)
    return agent


def test_run_agent_reports_every_step() -> None:
    """With on_step given, the agent streams and reports what it is doing."""
    updates = [
        {
            "model": {
                "messages": [
                    AIMessage(
                        content="Let me look at the service.",
                        tool_calls=[
                            {
                                "name": "list_service_files",
                                "args": {"service": "snuba"},
                                "id": "1",
                                "type": "tool_call",
                            }
                        ],
                    )
                ]
            }
        },
        {
            "tools": {
                "messages": [
                    ToolMessage(
                        content="deployment.yaml\n_values.yaml",
                        name="list_service_files",
                        tool_call_id="1",
                    )
                ]
            }
        },
        {"model": {"messages": [AIMessage(content="Done, it has two files.")]}},
    ]

    steps: list[AgentStep] = []
    with (
        patch("libsentrykube.agent.ChatOpenAI"),
        patch("libsentrykube.agent.create_agent") as mock_create_agent,
        patch("libsentrykube.agent.build_tools"),
    ):
        mock_create_agent.return_value = _streaming_agent(updates)

        response = run_agent(
            "how many files?",
            region="us",
            config=AgentConfig(api_key="TEST_KEY"),
            on_step=steps.append,
        )

    # The final model message is the answer, not the intermediate reasoning.
    assert response == "Done, it has two files."

    assert [step.kind for step in steps] == [
        "thought",
        "tool_call",
        "tool_result",
        "thought",
    ]
    assert steps[0].message == "Let me look at the service."
    assert steps[1].tool == "list_service_files"
    assert steps[1].tool_input == {"service": "snuba"}
    assert steps[2].tool == "list_service_files"
    assert "deployment.yaml" in (steps[2].detail or "")
    # The count describes the whole result, not the truncated detail.
    assert "2 line(s)" in steps[2].message


def test_run_agent_reports_tool_call_without_reasoning() -> None:
    """A model message can call a tool without saying anything first."""
    updates = [
        {
            "model": {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "list_resources",
                                "args": {"service": "snuba"},
                                "id": "1",
                                "type": "tool_call",
                            }
                        ],
                    )
                ]
            }
        },
    ]

    steps: list[AgentStep] = []
    with (
        patch("libsentrykube.agent.ChatOpenAI"),
        patch("libsentrykube.agent.create_agent") as mock_create_agent,
        patch("libsentrykube.agent.build_tools"),
    ):
        mock_create_agent.return_value = _streaming_agent(updates)

        run_agent(
            "list them",
            region="us",
            config=AgentConfig(api_key="TEST_KEY"),
            on_step=steps.append,
        )

    assert [step.kind for step in steps] == ["tool_call"]


def test_run_agent_truncates_long_tool_results() -> None:
    updates = [
        {
            "tools": {
                "messages": [
                    ToolMessage(
                        content="x" * 5000, name="render_resource", tool_call_id="1"
                    )
                ]
            }
        },
    ]

    steps: list[AgentStep] = []
    with (
        patch("libsentrykube.agent.ChatOpenAI"),
        patch("libsentrykube.agent.create_agent") as mock_create_agent,
        patch("libsentrykube.agent.build_tools"),
    ):
        mock_create_agent.return_value = _streaming_agent(updates)

        run_agent(
            "render it",
            region="us",
            config=AgentConfig(api_key="TEST_KEY"),
            on_step=steps.append,
        )

    detail = steps[0].detail or ""
    assert len(detail) < 5000
    assert detail.endswith("...")


def test_run_agent_does_not_stream_without_a_callback() -> None:
    """Without on_step the agent is invoked in one shot, as before."""
    with (
        patch("libsentrykube.agent.ChatOpenAI"),
        patch("libsentrykube.agent.create_agent") as mock_create_agent,
        patch("libsentrykube.agent.build_tools"),
    ):
        mock_create_agent.return_value = _mock_agent("42")

        run_agent("hi", region="us", config=AgentConfig(api_key="TEST_KEY"))

    mock_create_agent.return_value.stream.assert_not_called()
    mock_create_agent.return_value.invoke.assert_called_once()
