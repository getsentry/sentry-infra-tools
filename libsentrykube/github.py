"""GitHub glue: token resolution and resilient raw-file fetching.

`raw.githubusercontent.com` 404s (rather than 403s) on unauthenticated
requests to private repos, to avoid leaking their existence. Callers that
need a file from a private repo should go through `fetch_raw_file`, which
tries several ways to authenticate before giving up.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from urllib.error import URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen


class GithubFetchError(Exception):
    """Raised when every available method to fetch GitHub content fails."""


def resolve_github_token() -> str | None:
    """Best-effort GitHub token, preferring an explicit env var over `gh`'s.

    Checks `GITHUB_TOKEN`/`GH_TOKEN` first, then falls back to `gh auth
    token` if the `gh` CLI is installed and authenticated. Returns None if
    neither yields a token.
    """

    for env_var in ("GITHUB_TOKEN", "GH_TOKEN"):
        token = os.environ.get(env_var)
        if token:
            return token

    if shutil.which("gh") is None:
        return None

    try:
        result = subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _parse_raw_githubusercontent_url(url: str) -> tuple[str, str, str, str] | None:
    """Best-effort split of a raw.githubusercontent.com URL into
    (owner, repo, ref, path), for use with the `gh api` contents endpoint.

    Returns None if the URL isn't a raw.githubusercontent.com URL, or its
    ref/path can't be split unambiguously.
    """

    parsed = urlparse(url)
    if parsed.netloc != "raw.githubusercontent.com":
        return None

    segments = [segment for segment in parsed.path.split("/") if segment]
    if len(segments) < 3:
        return None
    owner, repo, *rest = segments

    if rest[:2] in (["refs", "heads"], ["refs", "tags"]):
        rest = rest[2:]
    if len(rest) < 2:
        return None
    ref, *path_segments = rest
    return owner, repo, ref, "/".join(path_segments)


def _fetch_via_gh_api(owner: str, repo: str, ref: str, path: str) -> bytes:
    if shutil.which("gh") is None:
        raise GithubFetchError("gh CLI not found on PATH")

    # `ref` must be part of the endpoint's query string rather than passed via
    # `-F`/`-f`: combined with the raw Accept header below, gh's own query
    # handling for `-F`/`-f` fields returns a 404 instead of the file content.
    endpoint = (
        f"repos/{owner}/{repo}/contents/{quote(path)}?ref={quote(ref, safe='')}"
    )
    try:
        result = subprocess.run(
            [
                "gh",
                "api",
                endpoint,
                "-H",
                "Accept: application/vnd.github.raw",
            ],
            capture_output=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GithubFetchError(f"gh api invocation failed: {exc}") from exc
    if result.returncode != 0:
        raise GithubFetchError(
            f"gh api exited {result.returncode}: "
            f"{result.stderr.decode(errors='replace').strip()}"
        )
    return result.stdout


def _fetch_via_url(url: str, *, token: str | None) -> bytes:
    headers = {"User-Agent": "sentry-kube"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    try:
        request = Request(url, headers=headers)
        with urlopen(request, timeout=15) as response:
            return response.read()
    except (OSError, URLError) as exc:
        raise GithubFetchError(f"{url}: {exc}") from exc


def fetch_raw_file(url: str) -> bytes:
    """Fetch a file's raw bytes from GitHub, trying multiple approaches.

    `url` is expected to be a `raw.githubusercontent.com` URL, possibly to a
    private repo. Tries, in order: `gh api` (handles private-repo auth
    transparently when `gh` is authenticated), an authenticated request to
    `url` (token from `GITHUB_TOKEN`/`GH_TOKEN` or `gh auth token`), then an
    anonymous request to `url`. Raises GithubFetchError, combining every
    attempt's failure, if all of them fail.
    """

    errors: list[str] = []

    components = _parse_raw_githubusercontent_url(url)
    if components is not None:
        try:
            return _fetch_via_gh_api(*components)
        except GithubFetchError as exc:
            errors.append(f"gh api: {exc}")

    token = resolve_github_token()
    if token is not None:
        try:
            return _fetch_via_url(url, token=token)
        except GithubFetchError as exc:
            errors.append(f"authenticated fetch: {exc}")

    try:
        return _fetch_via_url(url, token=None)
    except GithubFetchError as exc:
        errors.append(f"anonymous fetch: {exc}")

    raise GithubFetchError(f"Unable to fetch {url} via any method: " + "; ".join(errors))
