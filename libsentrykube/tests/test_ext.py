from unittest.mock import patch

from libsentrykube.ext import (
    build_annotation_data,
    build_label_data,
    format_docs,
    format_people,
    format_slack_channels,
    format_slos,
    format_teams,
)


mock_service_data = {
    "alert_slack_channels": ["feed-datdog"],
    "aspiring_domain_experts": [],
    "component": "component",
    "dashboard": "https://app.datadoghq.com/dashboard/gbx-2ai-7ud/symbolicator",
    "docs": {"symbolic repo": "https://github.com/getsentry/symbolic"},
    "domain_experts": [
        {"email": "arpad.borsos@sentry.io", "name": "\u00c1rp\u00e1d Borsos"},
        {"email": "sebastian.zivota@sentry.io", "name": "Sebastian Zivota"},
    ],
    "escalation": "https://sentry.pagerduty.com/escalation_policies#P7ILL7Z",
    "id": "symbolicator",
    "name": "Symbolicator",
    "notes": None,
    "production_readiness_docs": [],
    "slack_channels": ["discuss-processing"],
    "slos": [
        "https://app.datadoghq.com/slo/manage?slo_id=fcba53bc887259949faeeb666e7f7745",
        "https://app.datadoghq.com/slo/manage?slo_id=bf5b567bf7125fe3b33f2bc2f2f94409&tab=status_and_history&timeframe=7d",
    ],
    "teams": [{"display_name": "Processing", "id": "processing", "tags": []}],
    "tier": 0,
}


def test_build_annotation_data_invalid_id():
    expected = {
        "alertSlackChannels": "",
        "aspiringDomainExperts": "",
        "component": "",
        "dashboard": "",
        "docs": "",
        "domainExperts": "",
        "escalation": "",
        "id": "",
        "name": "",
        "slackChannels": "",
        "slos": "",
        "teams": "",
        "tier": "",
    }
    assert build_annotation_data(service_registry_id="fooooo") == expected


def test_build_label_data_invalid_id():
    expected = {
        "service_registry_component": "unknown",
        "service_registry_id": "unknown",
        "service_registry_primary_team": "unknown",
        "service_registry_tier": "unknown",
    }
    assert build_label_data(service_registry_id="fooooo") == expected


@patch("libsentrykube.ext.get_service_registry_data", return_value=mock_service_data)
def test_build_annotation_data_symbolicator(mock_get_service_registry_data):
    expected = {
        "alertSlackChannels": "#feed-datdog",
        "aspiringDomainExperts": "",
        "component": "component",
        "dashboard": "https://app.datadoghq.com/dashboard/gbx-2ai-7ud/symbolicator",
        "docs": "symbolic repo (https://github.com/getsentry/symbolic)",
        "domainExperts": "Árpád Borsos (arpad.borsos@sentry.io), Sebastian Zivota (sebastian.zivota@sentry.io)",
        "escalation": "https://sentry.pagerduty.com/escalation_policies#P7ILL7Z",
        "id": "symbolicator",
        "name": "Symbolicator",
        "slackChannels": "#discuss-processing",
        "slos": "https://app.datadoghq.com/slo/manage?slo_id=fcba53bc887259949faeeb666e7f7745, https://app.datadoghq.com/slo/manage?slo_id=bf5b567bf7125fe3b33f2bc2f2f94409&tab=status_and_history&timeframe=7d",
        "teams": "Processing (processing) tags={}",
        "tier": "0",
    }

    assert build_annotation_data(service_registry_id="symbolicator") == expected
    mock_get_service_registry_data.assert_called_once_with(
        service_registry_id="symbolicator"
    )


@patch("libsentrykube.ext.get_service_registry_data", return_value=mock_service_data)
def test_build_label_data_symbolicator(mock_get_service_registry_data):
    expected = {
        "service_registry_component": "component",
        "service_registry_id": "symbolicator",
        "service_registry_primary_team": "processing",
        "service_registry_tier": "0",
    }

    assert build_label_data(service_registry_id="symbolicator") == expected
    mock_get_service_registry_data.assert_called_once_with(
        service_registry_id="symbolicator"
    )


def test_format_docs_empty():
    assert format_docs(docs={}) == ""


def test_format_docs_single():
    assert (
        format_docs(
            docs={
                "data model": "https://www.notion.so/sentry/Replay-Data-Model-and-Access-Patterns-8febf04f64a44e51b62af5af8ce1f4e6",
            }
        )
        == "data model (https://www.notion.so/sentry/Replay-Data-Model-and-Access-Patterns-8febf04f64a44e51b62af5af8ce1f4e6)"
    )


def test_format_docs_multi():
    assert (
        format_docs(
            docs={
                "data model": "https://www.notion.so/sentry/Replay-Data-Model-and-Access-Patterns-8febf04f64a44e51b62af5af8ce1f4e6",
                "ingestion architecture": "https://www.notion.so/sentry/Replay-Ingestion-Architecture-bcbaa560346940ddb725fb0337b7f469",
            }
        )
        == "data model (https://www.notion.so/sentry/Replay-Data-Model-and-Access-Patterns-8febf04f64a44e51b62af5af8ce1f4e6), ingestion architecture (https://www.notion.so/sentry/Replay-Ingestion-Architecture-bcbaa560346940ddb725fb0337b7f469)"
    )


def test_format_people_empty():
    assert format_people(people=[]) == ""


def test_format_people_single():
    people = [
        {
            "name": "John A. MacDonald",
            "email": "johnnyapples@sentry.io",
        },
    ]
    assert format_people(people=people) == "John A. MacDonald (johnnyapples@sentry.io)"


def test_format_people_multi():
    people = [
        {
            "name": "John A. MacDonald",
            "email": "johnnyapples@sentry.io",
        },
        {
            "name": "A. Lovelace",
            "email": "lovie@sentry.io",
        },
    ]
    assert (
        format_people(people=people)
        == "John A. MacDonald (johnnyapples@sentry.io), A. Lovelace (lovie@sentry.io)"
    )


def test_format_slack_channels_empty():
    assert format_slack_channels(slack_channels=[]) == ""


def test_format_slack_channels_single():
    assert format_slack_channels(slack_channels=["discuss-foo"]) == "#discuss-foo"


def test_format_slack_channels_multi():
    assert (
        format_slack_channels(slack_channels=["discuss-foo", "feed-bar"])
        == "#discuss-foo, #feed-bar"
    )


def test_format_slos_empty():
    assert format_slos(slos=[]) == ""


def test_format_slos_single():
    slos = [
        "https://app.datadoghq.com/slo/manage?slo_id=a1&timeframe=30d",
    ]
    assert (
        format_slos(slos=slos)
        == "https://app.datadoghq.com/slo/manage?slo_id=a1&timeframe=30d"
    )


def test_format_slos_multi():
    slos = [
        "https://app.datadoghq.com/slo/manage?slo_id=a1&timeframe=30d",
        "https://app.datadoghq.com/slo/manage?slo_id=b2&timeframe=7d",
    ]
    assert (
        format_slos(slos=slos)
        == "https://app.datadoghq.com/slo/manage?slo_id=a1&timeframe=30d, https://app.datadoghq.com/slo/manage?slo_id=b2&timeframe=7d"
    )


def test_format_teams_empty():
    assert format_teams(teams=[]) == ""


def test_format_teams_single():
    teams = [
        {
            "display_name": "Ultra Mega Team",
            "id": "ultra_mega",
            "tags": [],
        },
    ]
    assert format_teams(teams=teams) == "Ultra Mega Team (ultra_mega) tags={}"


def test_format_teams_multi():
    teams = [
        {
            "display_name": "Ultra Mega Team",
            "id": "ultra_mega",
            "tags": [],
        },
        {
            "display_name": "Mid Team",
            "id": "mid",
            "tags": [],
        },
    ]
    assert (
        format_teams(teams=teams)
        == "Ultra Mega Team (ultra_mega) tags={}, Mid Team (mid) tags={}"
    )


def test_format_teams_no_tags():
    teams = [
        {
            "display_name": "Ultra Mega Team",
            "id": "ultra_mega",
            "tags": [],
        },
    ]
    assert format_teams(teams=teams) == "Ultra Mega Team (ultra_mega) tags={}"


def test_format_teams_single_tag():
    teams = [
        {"display_name": "Ultra Mega Team", "id": "ultra_mega", "tags": ["beast"]},
    ]
    assert format_teams(teams=teams) == "Ultra Mega Team (ultra_mega) tags={beast}"


def test_format_teams_multi_tags():
    teams = [
        {
            "display_name": "Ultra Mega Team",
            "id": "ultra_mega",
            "tags": ["beast", "kumbaya"],
        },
    ]
    assert (
        format_teams(teams=teams) == "Ultra Mega Team (ultra_mega) tags={beast,kumbaya}"
    )
