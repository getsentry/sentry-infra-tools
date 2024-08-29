import pytest
from unittest.mock import MagicMock, patch
from libsentrykube.jira import (
    JiraConfig,
    _create_jira_issue,
    _find_jira_issue,
    _update_jira_issue,
    _add_jira_comment,
    JiraApiException,
)
from requests.auth import HTTPBasicAuth


@pytest.fixture(autouse=True)
def setup():
    url = "https://test.atlassian.net"
    project_key = "TEST"
    user_email = "test@test.com"
    api_token = "test_token"
    return JiraConfig(url, project_key, user_email, api_token)


def test_create_issue_success(setup):
    mock_response = MagicMock()
    jiraConf = setup
    mock_response.status_code = 201
    mock_response.json.return_value = {"key": "JIRA-123"}
    with patch("requests.post", return_value=mock_response) as mock_post:
        region = "s4s"
        service = "snuba"
        body = ["tokyo drift"]

        response = _create_jira_issue(jiraConf, region, service, body)
        mock_post.assert_called_once_with(
            "https://test.atlassian.net/rest/api/2/issue",
            json={
                "fields": {
                    "project": {"key": "TEST"},
                    "summary": f"[Drift Detection]: {region} {service} drifted",
                    "description": f"There has been drift detected on {service} for {region}.\n\n{body}",
                    "issuetype": {"name": "Task"},
                    "labels": [
                        f"region:{region}",
                        f"service:{service}",
                        "issue_type:drift_detection",
                    ],
                }
            },
            auth=HTTPBasicAuth("test@test.com", "test_token"),
            headers={"Content-Type": "application/json"},
        )

        assert response.json()["key"] == "JIRA-123"
        assert response.status_code == 201


def test_create_issue_failure(setup):
    mock_response = MagicMock()
    mock_response.status_code = 400
    jiraConf = setup

    with patch("requests.post", return_value=mock_response) as mock_post:
        region = "saas"
        service = "relay"
        body = ["slowly drifting"]

        with pytest.raises(JiraApiException):
            _create_jira_issue(jiraConf, region, service, body)
        mock_post.assert_called_once_with(
            "https://test.atlassian.net/rest/api/2/issue",
            json={
                "fields": {
                    "project": {"key": "TEST"},
                    "summary": f"[Drift Detection]: {region} {service} drifted",
                    "description": f"There has been drift detected on {service} for {region}.\n\n{body}",
                    "issuetype": {"name": "Task"},
                    "labels": [
                        f"region:{region}",
                        f"service:{service}",
                        "issue_type:drift_detection",
                    ],
                }
            },
            auth=HTTPBasicAuth("test@test.com", "test_token"),
            headers={"Content-Type": "application/json"},
        )


def test_update_ticket_success(setup):
    mock_response = MagicMock()
    mock_response.status_code = 204
    jiraConf = setup

    with patch("requests.put", return_value=mock_response) as mock_put:
        issue_key = "JIRA-123"
        region = "saas"
        service = "relay"
        body = ["toyko drift"]

        response = _update_jira_issue(jiraConf, region, service, body, issue_key)
        mock_put.assert_called_once_with(
            f"https://test.atlassian.net/rest/api/2/issue/{issue_key}",
            json={
                "fields": {
                    "description": f"There has been drift detected on {service} for {region}.\n\n{body}"
                }
            },
            auth=HTTPBasicAuth("test@test.com", "test_token"),
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 204


def test_update_ticket_failure(setup):
    mock_response = MagicMock()
    jiraConf = setup

    with patch("requests.put", return_value=mock_response):
        issue_key = "JIRA-123"
        region = "saas"
        service = "relay"
        body = ["update: drift persistence noticed"]
        with pytest.raises(JiraApiException):
            _update_jira_issue(jiraConf, issue_key, region, service, body)


def test_find_jira_issue_success(setup):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"issues": [{"key": "JIRA-123"}]}
    jiraConf = setup

    with patch("requests.get", return_value=mock_response):
        region = "saas"
        service = "relay"

        issue_key = _find_jira_issue(jiraConf, region, service)
        assert issue_key == "JIRA-123"


def test_find_jira_issue_not_found(setup):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"issues": []}
    jiraConf = setup

    with patch("requests.get", return_value=mock_response):
        region = "saas"
        service = "relay"

        issue_key = _find_jira_issue(jiraConf, region, service)
        assert issue_key is None


def test_find_jira_issue_failure(setup):
    mock_response = MagicMock()
    mock_response.status_code = 400
    jiraConf = setup

    with patch("requests.get", return_value=mock_response):
        region = "saas"
        service = "relay"
        with pytest.raises(JiraApiException):
            _find_jira_issue(jiraConf, region, service)


def test_create_comment_success(setup):
    mock_response = MagicMock()
    mock_response.status_code = 201
    jiraConf = setup

    with patch("requests.post", return_value=mock_response) as mock_post:
        issue_key = "JIRA-123"
        test_comment = "test comment"
        response = _add_jira_comment(jiraConf, issue_key, test_comment)

        mock_post.assert_called_once_with(
            f"https://test.atlassian.net/rest/api/2/issue/{issue_key}/comment",
            json={"body": test_comment},
            auth=HTTPBasicAuth("test@test.com", "test_token"),
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 201


def test_create_comment_failure(setup):
    mock_response = MagicMock()
    mock_response.status_code = 400
    jiraConf = setup

    with patch("requests.post", return_value=mock_response):
        issue_key = "JIRA-123"
        test_comment = "test comment"
        with pytest.raises(JiraApiException):
            _add_jira_comment(jiraConf, issue_key, test_comment)
