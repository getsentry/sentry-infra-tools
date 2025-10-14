import os
import requests
from typing import Optional

LINEAR_API_URL = os.getenv("LINEAR_API_URL", "")
LINEAR_API_KEY = os.getenv("LINEAR_API_KEY", "")
LINEAR_TEAM_ID = "dc463b36-b68c-432a-a6d1-2beaab9b5cec"  # SRE
LINEAR_LABEL_DRIFT_ID = "dd3625d7-011b-4d52-8c74-086d539bb508"  # Ops Issue -> Drift
LINEAR_CUSTOMER_ID = "4b73f8a3-ef58-4ae7-95f1-489cf54f9766"  # Sentry Infrastructure

HEADERS = {"Authorization": LINEAR_API_KEY or "", "Content-Type": "application/json"}


def drift_issue(region: str, service: str, body: str) -> None:
    """
    Attempts to create an issue reporting drift on region and service if one
    does not already exist. Otherwise, it will add a comment that the drift
    still exists.
    """
    if issue_id := _find_issue(region, service):
        _add_comment(issue_id, "[UPDATED] Drift still present.")
    else:
        _create_issue(region, service, body)


def _generate_title(region: str, service: str) -> str:
    """
    Helper method to generate the issue title, which is also used for finding
    previous issues that are still open.
    """
    return f"[Drift Detection]: {region} {service} drifted"


def _create_issue(region: str, service: str, body: str) -> None:
    mutation = """
    mutation CreateIssue($input: IssueCreateInput!) {
        issueCreate(input: $input) {
            success
            issue {
            id
            title
            url
            labels {
                nodes {
                id
                name
                }
            }
            }
        }
    }
    """

    variables = {
        "input": {
            "teamId": LINEAR_TEAM_ID,
            "title": _generate_title(region, service),
            "description": f"There has been drift detected on {service} for {region}.\n\n{body}",
            "priority": 2,
            "labelIds": [LINEAR_LABEL_DRIFT_ID],
        }
    }

    response = requests.post(
        LINEAR_API_URL,
        headers=HEADERS,
        json={"query": mutation, "variables": variables},
    )

    resp = response.json()
    if "errors" in resp:
        raise Exception(f"Failed to create linear issue: {resp}")
    else:
        _associate_sentry_infrastructure(resp["data"]["issueCreate"]["issue"]["id"])


def _associate_sentry_infrastructure(issue_id: str) -> None:
    """
    Associates the Sentry Infrastructure customer to the issue for better classification
    of the issue.
    """
    mutation = """
    mutation CustomerNeedCreate($input: CustomerNeedCreateInput!) {
        customerNeedCreate(input: $input) {
            success
        }
    }
    """
    variables = {
        "input": {
            "customerId": LINEAR_CUSTOMER_ID,
            "issueId": issue_id,
        }
    }

    response = requests.post(
        LINEAR_API_URL,
        headers=HEADERS,
        json={"query": mutation, "variables": variables},
    )
    customer_link = response.json()
    if "errors" in customer_link:
        raise Exception(
            f"Failed to create link customer to issue: {customer_link['errors']}"
        )


def _find_issue(region: str, service: str) -> Optional[str]:
    """
    Looks for an open existing issue. Return issue key if issue exists, otherwise return nothing.
    """
    query = """
    query FindOpenIssues($title: String!, $teamId: ID!) {
      issues(
        filter: {
          title: { contains: $title },
          team: { id: { eq: $teamId } },
          state: { type: { nin: ["completed", "canceled"] } }
        }
      ) {
        nodes {
          id
          title
          identifier
          state {
            name
            type
          }
          assignee {
            name
          }
          url
        }
      }
    }
    """
    variables = {
        "title": _generate_title(region, service),
        "teamId": LINEAR_TEAM_ID,
    }
    response = requests.post(
        LINEAR_API_URL, headers=HEADERS, json={"query": query, "variables": variables}
    )
    resp = response.json()

    if "errors" in resp:
        raise Exception(f"Unable to search for linear issue: {resp['errors']}")
    else:
        if len(resp["data"]["issues"]["nodes"]) > 0:
            return resp["data"]["issues"]["nodes"][0]["id"]
        else:
            return None


def _add_comment(issue_id: str, comment: str) -> None:
    """
    Adds a comment to the existing Linear issue.
    """
    mutation = """
    mutation AddComment($input: CommentCreateInput!) {
      commentCreate(input: $input) {
        success
      }
    }
    """
    variables = {
        "input": {
            "issueId": issue_id,
            "body": comment,
        }
    }
    response = requests.post(
        LINEAR_API_URL,
        headers=HEADERS,
        json={"query": mutation, "variables": variables},
    )
    resp = response.json()
    if "errors" in resp:
        raise Exception(
            f"Failed to add comment: {resp['errors']}",
        )
