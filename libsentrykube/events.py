import copy
import getpass
import os
import sys
import time
from typing import List, Optional

import click
import httpx
from libsentrykube.config import Config
from libsentrykube.customer import get_region_config

DD_API_BASE = "https://api.datadoghq.com"

DATADOG_API_KEY = os.getenv("DATADOG_API_KEY") or os.getenv("DD_API_KEY")
DISABLED_VALUE = "NONE_AND_YES_I_AM_SURE"

SENTRY_KUBE_EVENT_SOURCE = "sentry-kube"
TERRAGRUNT_EVENT_SOURCE = "terragrunt"
# This category is supposed to be shared by other Sentry tools (terraform, salt, etc.) that report
# event to DataDog.
SENTRY_KUBE_EVENT_SOURCE_CATEGORY = "infra-tools"


def ensure_datadog_api_key_set() -> None:
    if DATADOG_API_KEY == DISABLED_VALUE:
        click.secho(
            "\nWARNING: DataDog API key is set to a special invalid value. No DataDog events will be reported.\n",
            bold=True,
        )
        return

    if not DATADOG_API_KEY:
        raise ValueError(
            "DataDog API key (DD_API_KEY variable) is not set. We require it now."
        )


def send_event_payload_to_datadog(payload: dict, quiet: bool = False) -> None:
    # API docs: https://docs.datadoghq.com/api/latest/events/#post-an-event
    res = httpx.post(
        f"{DD_API_BASE}/api/v1/events",
        headers={
            "DD-API-KEY": DATADOG_API_KEY,
        },
        json=payload,
    )
    res.raise_for_status()
    if not quiet:
        click.echo("\nReported the action to DataDog events:")
        click.echo(res.json()["event"]["url"])


def report_event_to_datadog(
    title: str, text: str, tags: dict, quiet: bool = False
) -> None:
    payload = {
        "title": title,
        "text": text,
        "tags": [f"{k}:{v}" for k, v in tags.items()],
        "date_happened": int(time.time()),
        "alert_type": "user_update",
    }
    return send_event_payload_to_datadog(payload, quiet)


def _markdown_text(text: str) -> str:
    return f"%%%\n{text}\n%%%"


def _get_sentry_region(region_name: str) -> str:
    _, region_config = get_region_config(Config(), region_name)
    return region_config.sentry_region


def report_terragrunt_event(
    cli_args: str,
    extra_tags: Optional[dict] = None,
    quiet: bool = False,
) -> None:
    # Find our slice under terragrunt/terraform
    if "terraform/" in os.getcwd():
        tgroot = "terraform"
        tgslice = os.getcwd().split("terraform/")[1]
        region = "us"
    elif "terragrunt/" in os.getcwd():
        tgroot = "terragrunt"
        tgslice = os.getcwd().split("terragrunt/")[1].split("/.terragrunt-cache/")[0]
        region = tgslice.split("/")[-1]
    else:
        raise RuntimeError("Unable to determine what slice you're running in.")

    sentry_region = Config().silo_regions[region].sentry_region

    user = getpass.getuser()

    tags = {
        "source": TERRAGRUNT_EVENT_SOURCE,
        "source_tool": TERRAGRUNT_EVENT_SOURCE,
        "source_category": SENTRY_KUBE_EVENT_SOURCE_CATEGORY,
        "sentry_user": user,
        "sentry_region": sentry_region,
        "terragrunt_root": tgroot,
        "terragrunt_slice": tgslice,
        "terragrunt_cli_args": cli_args,
        **(extra_tags if extra_tags is not None else {}),
    }

    report_event_to_datadog(
        title=f"terragrunt: Ran '{cli_args}' for slice '{tgslice}' in region '{sentry_region}'",
        text=_markdown_text(
            f"User **{user}** ran terragrunt '{cli_args}' for slice: **{tgslice}** "
        ),
        tags=tags,
        quiet=quiet,
    )


def report_event_for_service(
    customer_name: str,
    cluster_name: str,
    operation: str,
    service_name: str = "",
    secret_name: str = "",
    extra_tags: Optional[dict] = None,
    quiet: bool = False,
) -> None:
    user = getpass.getuser()
    sentry_region = _get_sentry_region(customer_name)
    command_line = " ".join(sys.argv)

    # Determine service_name from the manifest prefix
    if service_name == "kubectl":
        for split_point in [
            "daemonset",
            "ds",
            "deployment",
            "deploy",
            "namespace",
            "ns",
            "node",
            "no",
            "pod",
            "po",
            "secret",
            "service",
            "svc",
            "serviceaccount",
            "sa",
            "statefulset",
            "sts",
        ]:
            for extra_char in [" ", "/"]:
                if f"{split_point}{extra_char}" in command_line:
                    service_name = command_line.split(f"{split_point}{extra_char}")[1]
                    break

    tags = {
        "source": SENTRY_KUBE_EVENT_SOURCE,
        "source_tool": SENTRY_KUBE_EVENT_SOURCE,
        "source_category": SENTRY_KUBE_EVENT_SOURCE_CATEGORY,
        "customer_name": customer_name,
        "cluster_name": cluster_name,
        "sentry_user": user,
        "sentry_kube_operation": operation,
        "sentry_region": sentry_region,
        **(extra_tags if extra_tags is not None else {}),
    }

    msg = ""

    if service_name != "":
        tags["sentry_service"] = service_name
        msg = f"service: **{service_name}**"

    if secret_name != "":
        tags["sentry_secret_name"] = secret_name
        msg = f"secret: **{secret_name}**"

        # Try to determine the service name from the secret name, this will be messy
        if secret_name == "getsentry-secrets":
            service_name = "getsentry"
        else:
            service_name = (
                secret_name.replace("oauth-", "")
                .replace("service-", "")
                .replace("keda-", "")
            )

        tags["sentry_service"] = service_name

    report_event_to_datadog(
        title=_markdown_text(f"sentry-kube: Ran '{operation}' for {msg}"),
        text=_markdown_text(
            f"User **{user}** ran sentry-kube operation '{operation}' for {msg} "
            f"(region: **{sentry_region}**)\n\n"
            f"Command line: `{command_line}`"
        ),
        tags=tags,
        quiet=quiet,
    )


def report_event_for_service_list(
    customer_name: str,
    cluster_name: str,
    operation: str,
    services: List[str],
    extra_tags: Optional[dict] = None,
    quiet: bool = False,
) -> None:
    for service in services:
        report_event_for_service(
            customer_name=customer_name,
            cluster_name=cluster_name,
            operation=operation,
            service_name=service,
            extra_tags=copy.deepcopy(extra_tags),
            quiet=quiet,
        )
