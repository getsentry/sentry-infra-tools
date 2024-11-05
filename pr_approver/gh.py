import urllib.request
from typing import Sequence
from json import loads, dumps


def accept_pr(pr_number: int, body: str, token: str) -> None:
    """
    Accepts a PR with a message.
    """
    payload = {"body": body, "event": "APPROVE"}
    json_data = dumps(payload)
    data = json_data.encode("utf-8")
    req = urllib.request.Request(
        f"https://api.github.com/repos/getsentry/ops/pulls/{pr_number}/reviews",
        data=data,
        method="POST",
    )
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req) as response:
        assert response.status


def dismiss_acceptance(
    pr_number: int, body: str, user_login: str, token: str
) -> Sequence[int]:
    """
    Dismiss all the approved reviews done by the provided user
    on a specific PR.
    """
    reviews_req = urllib.request.Request(
        f"https://api.github.com/repos/getsentry/ops/pulls/{pr_number}/reviews",
        method="GET",
    )
    reviews_req.add_header("Accept", "application/vnd.github+json")
    reviews_req.add_header("Authorization", f"Bearer {token}")
    reviews_req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(reviews_req) as response:
        assert response.status == 200
        payload = response.read()
        resp = loads(payload.decode("utf-8"))

    ids_to_dismiss = [
        review["id"]
        for review in resp
        if review.get("user", {}).get("login") == user_login
        and review["state"] == "APPROVED"
    ]

    for id in ids_to_dismiss:
        payload = {"message": body}
        json_data = dumps(payload)
        data = json_data.encode("utf-8")
        reviews_req = urllib.request.Request(
            f"https://api.github.com/repos/getsentry/ops/pulls/{pr_number}/reviews/{id}/dismissals",
            method="PUT",
            data=data,
        )
        reviews_req.add_header("Accept", "application/vnd.github+json")
        reviews_req.add_header("Authorization", f"Bearer {token}")
        reviews_req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(reviews_req) as response:
            assert response.status == 200

    return ids_to_dismiss
