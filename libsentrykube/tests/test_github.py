import subprocess
from unittest.mock import MagicMock, patch

import pytest

from libsentrykube.github import (
    GithubFetchError,
    _fetch_via_gh_api,
    _parse_raw_githubusercontent_url,
    fetch_raw_file,
    resolve_github_token,
)

REPOS_JSON_URL = (
    "https://raw.githubusercontent.com/getsentry/"
    "sentry-options-automator/refs/heads/main/repos.json"
)


def test_resolve_github_token_prefers_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "env-token")

    assert resolve_github_token() == "env-token"


def test_resolve_github_token_falls_back_to_gh_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)

    with (
        patch("libsentrykube.github.shutil.which", return_value="/usr/bin/gh"),
        patch(
            "libsentrykube.github.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, "gh-token\n", ""),
        ) as mock_run,
    ):
        assert resolve_github_token() == "gh-token"

    assert mock_run.call_args.args[0] == ["gh", "auth", "token"]


def test_resolve_github_token_none_when_gh_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)

    with patch("libsentrykube.github.shutil.which", return_value=None):
        assert resolve_github_token() is None


def test_resolve_github_token_none_when_gh_auth_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)

    with (
        patch("libsentrykube.github.shutil.which", return_value="/usr/bin/gh"),
        patch(
            "libsentrykube.github.subprocess.run",
            return_value=subprocess.CompletedProcess([], 1, "", "not logged in"),
        ),
    ):
        assert resolve_github_token() is None


@pytest.mark.parametrize(
    "url,expected",
    [
        (REPOS_JSON_URL, ("getsentry", "sentry-options-automator", "main", "repos.json")),
        (
            "https://raw.githubusercontent.com/getsentry/getsentry/deadbeef/sentry-options/schemas/getsentry/schema.json",
            (
                "getsentry",
                "getsentry",
                "deadbeef",
                "sentry-options/schemas/getsentry/schema.json",
            ),
        ),
        ("https://example.com/getsentry/getsentry/main/repos.json", None),
        ("https://raw.githubusercontent.com/getsentry/getsentry", None),
    ],
)
def test_parse_raw_githubusercontent_url(url: str, expected: object) -> None:
    assert _parse_raw_githubusercontent_url(url) == expected


def test_fetch_via_gh_api_puts_ref_in_the_query_string() -> None:
    # `ref` must not be passed via `-F`/`-f`: combined with the raw Accept
    # header, gh's own field handling returns a 404 instead of the content.
    with (
        patch("libsentrykube.github.shutil.which", return_value="/usr/bin/gh"),
        patch(
            "libsentrykube.github.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, b"file bytes", b""),
        ) as mock_run,
    ):
        result = _fetch_via_gh_api("getsentry", "sentry-options-automator", "main", "repos.json")

    assert result == b"file bytes"
    args = mock_run.call_args.args[0]
    assert args[:2] == ["gh", "api"]
    assert "-F" not in args
    assert "-f" not in args
    assert args[2] == "repos/getsentry/sentry-options-automator/contents/repos.json?ref=main"


def test_fetch_via_gh_api_raises_when_gh_missing() -> None:
    with patch("libsentrykube.github.shutil.which", return_value=None):
        with pytest.raises(GithubFetchError):
            _fetch_via_gh_api("getsentry", "sentry-options-automator", "main", "repos.json")


def test_fetch_via_gh_api_raises_on_nonzero_exit() -> None:
    with (
        patch("libsentrykube.github.shutil.which", return_value="/usr/bin/gh"),
        patch(
            "libsentrykube.github.subprocess.run",
            return_value=subprocess.CompletedProcess([], 1, b"", b"gh: Not Found (HTTP 404)"),
        ),
    ):
        with pytest.raises(GithubFetchError, match="Not Found"):
            _fetch_via_gh_api("getsentry", "sentry-options-automator", "main", "repos.json")


def test_fetch_raw_file_uses_gh_api_first() -> None:
    with patch(
        "libsentrykube.github._fetch_via_gh_api", return_value=b"from gh api"
    ) as mock_gh_api:
        result = fetch_raw_file(REPOS_JSON_URL)

    assert result == b"from gh api"
    mock_gh_api.assert_called_once_with(
        "getsentry", "sentry-options-automator", "main", "repos.json"
    )


def test_fetch_raw_file_falls_back_to_authenticated_url_when_gh_api_fails() -> None:
    with (
        patch(
            "libsentrykube.github._fetch_via_gh_api",
            side_effect=GithubFetchError("gh unavailable"),
        ),
        patch("libsentrykube.github.resolve_github_token", return_value="a-token"),
        patch("libsentrykube.github._fetch_via_url") as mock_fetch_via_url,
    ):
        mock_fetch_via_url.return_value = b"from authenticated url"

        result = fetch_raw_file(REPOS_JSON_URL)

    assert result == b"from authenticated url"
    mock_fetch_via_url.assert_called_once_with(REPOS_JSON_URL, token="a-token")


def test_fetch_raw_file_falls_back_to_anonymous_url_last() -> None:
    with (
        patch(
            "libsentrykube.github._fetch_via_gh_api",
            side_effect=GithubFetchError("gh unavailable"),
        ),
        patch("libsentrykube.github.resolve_github_token", return_value=None),
        patch(
            "libsentrykube.github._fetch_via_url", return_value=b"from anonymous url"
        ) as mock_fetch_via_url,
    ):
        result = fetch_raw_file(REPOS_JSON_URL)

    assert result == b"from anonymous url"
    mock_fetch_via_url.assert_called_once_with(REPOS_JSON_URL, token=None)


def test_fetch_raw_file_raises_combined_error_when_everything_fails() -> None:
    with (
        patch(
            "libsentrykube.github._fetch_via_gh_api",
            side_effect=GithubFetchError("gh unavailable"),
        ),
        patch("libsentrykube.github.resolve_github_token", return_value="a-token"),
        patch(
            "libsentrykube.github._fetch_via_url",
            side_effect=GithubFetchError("network unreachable"),
        ),
    ):
        with pytest.raises(GithubFetchError, match="gh unavailable"):
            fetch_raw_file(REPOS_JSON_URL)
