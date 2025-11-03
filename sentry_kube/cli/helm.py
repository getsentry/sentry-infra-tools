from functools import wraps

import click

from libsentrykube.helm import (
    HelmException,
    apply as _helm_apply,
    check_helm_bin,
    diff as _helm_diff,
    render as _helm_render,
    render_values as _helm_render_values,
    rollback as _helm_rollback,
    materialize_values as _helm_materialize_values,
)
from libsentrykube.service import get_service_names

__all__ = ["helm"]


@click.group()
def helm():
    """
    Wrapper for helm commands.
    """
    pass


def allow_for_all_services(f):
    """
    This decorator adds `--all` and `--exclude`.
    """

    @wraps(f)
    @click.argument("services", nargs=-1, type=str)
    @click.option("--all", "-a", "all_", is_flag=True, help="Select all services.")
    @click.option(
        "--exclude",
        default="",
        type=str,
        help="Comma-delimited string of service names to exclude.",
    )
    def wrapper(*args, **kwargs):
        services = list(kwargs.get("services"))
        all_services = get_service_names(namespace="helm")
        if not all_services:
            raise click.UsageError("No services found.")

        all_services_pretty = "\n".join(f"- {s}" for s in sorted(all_services))

        if kwargs.pop("all_"):
            if services:
                raise click.BadArgumentUsage(
                    "You specified '--all' along with some service names, "
                    "what do you actually want?\n"
                    f"Services:\n{all_services_pretty}"
                )
            services = all_services
        elif not services:
            raise click.BadArgumentUsage(
                f"No service names provided. Services:\n{all_services_pretty}"
            )

        excludes = kwargs.pop("exclude")
        if excludes:
            for svc in excludes.split(","):
                services.remove(svc)

        kwargs["services"] = services

        return f(*args, **kwargs)

    return wrapper


def _render(ctx, services, release, namespace, raw, renderer):
    customer_name = ctx.obj.customer_name
    cluster_name = ctx.obj.cluster_name
    context_name = ctx.obj.context_name

    for service_name in services:
        yield renderer(
            customer_name,
            service_name,
            cluster_name,
            release=release,
            namespace=namespace,
            raw=raw,
            kctx=context_name,
        )


def _materialize(ctx, services, release, namespace):
    customer_name = ctx.obj.customer_name
    cluster_name = ctx.obj.cluster_name
    for service in services:
        _helm_materialize_values(
            customer_name, service, cluster_name, release=release, namespace=namespace
        )


@check_helm_bin
def _diff(ctx, services, release, namespace, app_version):
    customer_name = ctx.obj.customer_name
    cluster_name = ctx.obj.cluster_name
    context_name = ctx.obj.context_name

    for service_name in services:
        yield _helm_diff(
            customer_name,
            service_name,
            cluster_name,
            release=release,
            namespace=namespace,
            app_version=app_version,
            kctx=context_name,
        )


@check_helm_bin
def _apply(ctx, services, release, namespace, app_version, atomic, timeout):
    customer_name = ctx.obj.customer_name
    cluster_name = ctx.obj.cluster_name
    context_name = ctx.obj.context_name

    errored = False

    for service_name in services:
        try:
            for item in _helm_apply(
                customer_name,
                service_name,
                cluster_name,
                release=release,
                namespace=namespace,
                app_version=app_version,
                kctx=context_name,
                atomic=atomic,
                timeout=timeout,
            ):
                yield item
        except HelmException:
            errored = True

    if errored:
        raise click.Abort()


@check_helm_bin
def _rollback(ctx, services, release, namespace, timeout):
    customer_name = ctx.obj.customer_name
    cluster_name = ctx.obj.cluster_name
    context_name = ctx.obj.context_name

    errored = False

    for service_name in services:
        try:
            for item in _helm_rollback(
                customer_name,
                service_name,
                cluster_name,
                release=release,
                namespace=namespace,
                kctx=context_name,
                timeout=timeout,
            ):
                yield item
        except HelmException:
            errored = True

    if errored:
        raise click.Abort()


@helm.command()
@click.option("--release", help="Target a specific release")
@click.option("--namespace", help="Targets a specific namespace")
@click.option("--raw", is_flag=True)
@click.option("--pager/--no-pager", default=True)
@click.option("--values-only", is_flag=True, help="Render Helm values only")
@click.option("--materialize", is_flag=True)
@click.pass_context
@allow_for_all_services
def render(ctx, services, release, namespace, raw, pager, values_only, materialize):
    """
    Render helm service(s).

    This is non-destructive and just renders the service(s) to stdout.
    """

    if materialize:
        _materialize(ctx, services, release, namespace)
        return
    if values_only:
        rendered = _render(ctx, services, release, namespace, raw, _helm_render_values)
    else:
        rendered = check_helm_bin(_render)(
            ctx, services, release, namespace, raw, _helm_render
        )
    if pager:
        click.echo_via_pager(rendered)
    else:
        click.echo("".join(rendered))


@helm.command()
@click.option("--release", help="Target a specific release")
@click.option("--namespace", help="Targets a specific namespace")
@click.option("--app-version", help="Use a specific app version")
@click.option("--pager/--no-pager", default=True)
@click.pass_context
@allow_for_all_services
def diff(ctx, services, release, namespace, app_version, pager):
    """
    Render a diff between production and local configs, using a wrapper around
    "helm diff".

    This is non-destructive and tells you what would be applied, if
    anything, with your current changes.
    """

    click.echo(f"Rendering services: {', '.join(services)}")
    rendered = _diff(ctx, services, release, namespace, app_version)
    if pager:
        click.echo_via_pager(rendered)
    else:
        click.echo("".join(rendered))


@helm.command()
@click.option("--release", help="Target a specific release")
@click.option("--namespace", help="Targets a specific namespace")
@click.option("--app-version", help="Use a specific app version")
@click.option("--yes", "-y", is_flag=True)
@click.option(
    "--atomic/--no-atomic",
    default=True,
    help="Atomic apply (auto-rollback if goes wrong)",
)
@click.option("--timeout", default=300)
@click.pass_context
@allow_for_all_services
def apply(ctx, services, release, namespace, app_version, yes, atomic, timeout):
    """
    Apply helm service(s) to production
    """

    if not yes:
        click.echo(f"Rendering services: {', '.join(services)}")

        rendered = _diff(ctx, services, release, namespace, app_version)
        click.echo("".join(rendered))

        if not click.confirm(
            "Are you sure you want to apply this for region "
            f"{click.style(ctx.obj.customer_name, fg='yellow', bold=True)}"
            ", cluster "
            f"{click.style(ctx.obj.cluster_name, fg='yellow', bold=True)}"
            "?"
        ):
            raise click.Abort()

    for res in _apply(ctx, services, release, namespace, app_version, atomic, timeout):
        click.echo(res)


@helm.command()
@click.option("--release", help="Target a specific release")
@click.option("--namespace", help="Targets a specific namespace")
@click.option("--timeout", default=None)
@click.pass_context
@allow_for_all_services
def rollback(ctx, services, release, namespace, timeout):
    """
    Rollback helm service(s) to previous release
    """

    for res in _rollback(ctx, services, release, namespace, timeout):
        click.echo(res)
