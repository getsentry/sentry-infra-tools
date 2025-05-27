import os
import requests
from typing import cast, Optional

LINEAR_API_URL = os.getenv("LINEAR_API_URL")
LINEAR_API_KEY = os.getenv("LINEAR_API_KEY")
LINEAR_TEAM_ID = os.getenv("LINEAR_TEAM_ID")
LINEAR_LABEL_DRIFT_ID = "dd3625d7-011b-4d52-8c74-086d539bb508"


def drift_issue(region: str, service: str, body: str) -> None:
    """
    Attempts to create an issue reporting drift on region and service if the relevant
    environment variables are set. Otherwise this does nothing.
    """

    if issue_id := _find_issue(region, service):
        _add_comment(issue_id, "[UPDATED] Drift still present. ")
    else:
        _create_issue(region, service, body)


def _create_issue(region: str, service: str, body: str) -> requests.Response:
    headers = {
        "Authorization": LINEAR_API_KEY,
        "Content-Type": "application/json"
    }

    # GraphQL mutation
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
            "title": f"[Drift Detection]: {region} {service} drifted",
            "description": f"There has been drift detected on {service} for {region}.\n\n{body}",
            "priority": 2,
            "labelIds": [LINEAR_LABEL_DRIFT_ID]
        }
    }

    response = requests.post(LINEAR_API_URL, headers=headers, json={
        "query": mutation,
        "variables": variables
    })
    data = response.json()

    if 'errors' in data:
        raise Exception(f"Failed to create linear issue: {data['errors']}")
    else:
        return data
    

def _find_issue(region: str, service: str) -> Optional[str]:
    """
    Looks for an open existing issue. Return issue key if issue exists, otherwise return nothing.
    """
    headers = {
        "Authorization": LINEAR_API_KEY,
        "Content-Type": "application/json"
    }

    # GraphQL mutation
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
        "title": f"[Drift Detection]: {region} {service} drifted",
        "teamId": LINEAR_TEAM_ID,
    }

    response = requests.post(LINEAR_API_URL, headers=headers, json={
        "query": query,
        "variables": variables
    })
    data = response.json()

    if 'errors' in data:
        raise Exception(f"Unable to search for linear issue: {data['errors']}")
    else:
        return data['issues']['nodes'][0]
    

def _add_comment(issue_id: str) -> Optional[str]:
    mutation = """
    mutation AddComment($input: CommentCreateInput!) {
      commentCreate(input: $input) {
        success
        comment {
          id
          body
        }
      }
    }
    """
    variables = {
        "input": {
            "issueId": issue_id,
            "body": "[UPDATE] Drift still present."
        }
    }
    headers = {
        "Authorization": LINEAR_API_KEY,
        "Content-Type": "application/json"
    }
    response = requests.post(LINEAR_API_URL, headers=headers, json={
        "query": mutation,
        "variables": variables
    })
    data = response.json()
    if 'errors' in data:
        raise Exception("Failed to add comment:", response)