import requests
from requests.auth import HTTPBasicAuth
from typing import cast, Optional

MAX_JIRA_DESCRIPTION_LENGTH = 32000


class JiraConfig:
    def __init__(self, url: str, project_key: str, user_email: str, api_token: str):
        self.url = url
        self.project_key = project_key
        self.user_email = user_email
        self.api_token = api_token


class JiraApiException(Exception):
    def __init__(self, stderr: str) -> None:
        self.message = stderr
        super().__init__(self.message)


def drift_jira_issue(jira: JiraConfig, region: str, service: str, body: str) -> None:
    """
    Attempts to create a jira issue reporting drift on region and service if the relevant
    environment variables are set. Otherwise this does nothing.
    """

    if key := _find_jira_issue(jira, region, service):
        _update_jira_issue(jira, region, service, body, key)
        _add_jira_comment(jira, key, "[UPDATED] Drift still present. ")
    else:
        _create_jira_issue(jira, region, service, body)


def _create_jira_issue(
    jira: JiraConfig, region: str, service: str, body: str
) -> requests.Response:
    """
    Attempts to create a new jira issue.
    """
    api_url = f"{jira.url}/rest/api/2/issue"
    issue_data = {
        "fields": {
            "project": {"key": jira.project_key},
            "summary": f"[Drift Detection]: {region} {service} drifted",
            "description": f"There has been drift detected on {service} for {region}.\n\n{body}",
            "issuetype": {"name": "Task"},
            "labels": [
                f"region:{region}",
                f"service:{service}",
                "issue_type:drift_detection",
            ],
        }
    }

    response = requests.post(
        api_url,
        json=issue_data,
        auth=HTTPBasicAuth(jira.user_email, jira.api_token),
        headers={"Content-Type": "application/json"},
    )

    if response.status_code == 201:
        return response
    else:
        raise JiraApiException(
            f"Failed to create issue: {response.status_code}, {response.text}"
        )


def _update_jira_issue(
    jira: JiraConfig,
    region: str,
    service: str,
    body: str,
    issue_key: str,
) -> requests.Response:
    """
    Attempts to update a jira issue given the issue key.
    """
    api_url = f"{jira.url}/rest/api/2/issue/{issue_key}"
    issue_data = {
        "fields": {
            "description": f"There has been drift detected on {service} for {region}.\n\n{body}"
        }
    }
    response = requests.put(
        api_url,
        json=issue_data,
        auth=HTTPBasicAuth(jira.user_email, jira.api_token),
        headers={"Content-Type": "application/json"},
    )
    if response.status_code == 204:
        return response
    else:
        raise JiraApiException(
            f"Failed to update issue: {response.status_code}, {response.text}"
        )


def _add_jira_comment(
    jira: JiraConfig, issue_key: str, comment: str
) -> requests.Response:
    """
    Adds a comment to the given jira issue.
    """
    api_url = f"{jira.url}/rest/api/2/issue/{issue_key}/comment"
    payload = {"body": comment}
    response = requests.post(
        api_url,
        json=payload,
        auth=HTTPBasicAuth(jira.user_email, jira.api_token),
        headers={
            "Content-Type": "application/json",
        },
    )
    if response.status_code == 201:
        return response
    else:
        raise JiraApiException(
            f"Failed to update issue: {response.status_code}, {response.text}"
        )


def _find_jira_issue(jira: JiraConfig, region: str, service: str) -> Optional[str]:
    """
    Looks for an open existing jira issue. Return issue key if issue exists, otherwise return nothing.
    """
    api_url = f"{jira.url}/rest/api/2/search"

    labels_region = f"region:{region}"
    labels_service = f"service:{service}"
    issue_type_label = "issue_type:drift_detection"
    jql = (
        f'project = "{jira.project_key}" '
        f'AND labels = "{labels_region}" '
        f'AND labels = "{labels_service}" '
        f'AND labels = "{issue_type_label}" '
        'AND status != "CLOSED" '
        'AND status != "DONE"'
    )

    params = {"jql": jql, "fields": "id,key,summary,status"}

    response = requests.get(
        api_url,
        headers={"Accept": "application/json"},
        auth=HTTPBasicAuth(jira.user_email, jira.api_token),
        params=params,
    )
    if response.status_code == 200:
        issues = response.json()["issues"]
        if issues:
            return cast(str, issues[0]["key"])
        return None
    else:
        raise JiraApiException(
            f"Failed to search issues: {response.status_code}, {response.text}"
        )
