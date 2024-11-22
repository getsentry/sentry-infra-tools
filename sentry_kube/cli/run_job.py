import io
from time import sleep

import click

from libsentrykube.events import report_event_for_service
from libsentrykube.utils import die

__all__ = ("run_job",)


@click.command()
@click.option("--yes", "-y", is_flag=True)
@click.option(
    "--quiet", "-q", is_flag=True, help="topicctl only - silences irrelevant log lines"
)
@click.option("--arg", "-a", multiple=True, help="add additional arg to Job args")
@click.option(
    "--kwarg", "-k", multiple=True, help="add additional keyword arg to Job args"
)
@click.argument("service-name", nargs=1, type=str, required=True)
@click.argument("job-name", nargs=1, type=str, required=True)
@click.pass_context
def run_job(ctx, job_name, arg, kwarg, service_name, yes, quiet):
    """\b
    Apply a service's job to production.

    \b
    Example:
     $ sentry-kube run-job sentry upgrade
    """
    customer_name = ctx.obj.customer_name
    cluster_name = ctx.obj.cluster_name

    from kubernetes import client

    from libsentrykube.kube import apply as kube_apply
    from libsentrykube.kube import collect_diffs, collect_kube_resources

    kube_resources = [
        *collect_kube_resources(
            customer_name,
            service_name,
            cluster_name,
            # Skip GKE CRDs which don't work with our
            # client-side kubernetes API management
            skip_kinds=("BackendConfig", "ManagedCertificate", "VerticalPodAutoscaler"),
            kind_matches=("Job",),
            name_matches=(job_name,),
            extra_args=arg,
            extra_kwargs=kwarg,
        )
    ]

    if len(kube_resources) == 0:
        die(
            "No matching kubernetes objects were found.\n"
            "You may have mistyped the job name."
        )

    if len(kube_resources) > 1:
        die(
            "Too many kubernetes objects were found (expected exactly 1 job).\n"
            "You may have duplicate job specs."
        )

    diffs = [*collect_diffs(kube_resources)]
    if len(diffs) == 0:
        click.echo("No differences to apply.")
        return

    assert len(diffs) == 1
    job_diff = diffs[0]

    if not (
        yes
        or click.confirm(
            "Are you sure you want to apply this for region "
            f"{click.style(customer_name, fg='yellow', bold=True)}"
            ", cluster "
            f"{click.style(ctx.obj.cluster_name, fg='yellow', bold=True)}"
            "?"
        )
    ):
        raise click.Abort()

    from libsentrykube.kube import kube_get_client

    k8s_client = kube_get_client()

    kube_apply([job_diff])

    api = client.CoreV1Api(k8s_client)
    name = job_diff.name
    namespace = job_diff.namespace

    try:
        # Find our Pod that is spawned from this Job
        pods = api.list_namespaced_pod(
            namespace=namespace, label_selector=f"job-name={name}"
        ).items
        # It's possible for Jobs to spawn more than one Pod,
        # but we don't have a use case for that yet.
        assert len(pods) == 1, len(pods)
        pod_name = pods[0].metadata.name
        click.echo(f"Waiting for Pod {pod_name}")

        # Now we begin our loop and shitty state machine
        read_logs = False
        while True:
            pod = api.read_namespaced_pod(namespace=namespace, name=pod_name)

            # If we have successfully read our logs, we're ready to report
            # status.
            if read_logs:
                if pod.status.phase == "Failed":
                    die("Failed.")

                try:
                    report_event_for_service(
                        customer_name,
                        ctx.obj.cluster_name,
                        operation=f"run-job {job_name}",
                        service_name=service_name,
                        quiet=ctx.obj.quiet_mode,
                    )
                except Exception as e:
                    click.echo("!! Could not report an event to DataDog:")
                    click.secho(e, bold=True)

                return

            if pod.status.phase == "Pending":
                # If we are in a Pending state, we need to also check
                # if we're in the ContainerCreating phase of the container(s) inside.
                # For any other event, like, an ErrImagePull, the Pod will
                # forever be left in Pending, but the container inside has errored.
                # So the only valid state to continue waiting is ContainerCreating.
                # Any other state is considered a failure.
                flag = True
                while True:
                    # reread pod state
                    pod = api.read_namespaced_pod(namespace=namespace, name=pod_name)
                    # sometimes, this api call can return with container_statuses None
                    # in which case we'll have to poll until it's populated
                    if pod.status.container_statuses is None:
                        flag = False
                    else:
                        for container_status in pod.status.container_statuses:
                            if container_status.ready:
                                break

                            if container_status.state.terminated:
                                reason = container_status.state.terminated.reason
                                print(
                                    f"""Container {container_status.name} terminated due to reason {reason}"""  # noqa
                                )
                                # We don't die here; often the reason is just
                                # "Error" and pod logs are much more descriptive.
                                break

                            reason = container_status.state.waiting.reason
                            click.echo(
                                f"waiting on container {container_status.name} "
                                f"({reason})"
                            )
                            if reason in ("ContainerCreating", "PodInitializing"):
                                flag = False
                                break
                            else:
                                die(f"Failed due to reason {reason}")
                    if flag:
                        break
                    flag = True
                    sleep(1)

            if pod.status.phase in ("Running", "Succeeded", "Failed"):
                container_statuses = pod.status.container_statuses

                for container in container_statuses:
                    # Remove standard secondary containers from list
                    # so you aren't prompted on every run-job
                    if container.name in [
                        "envoy",
                    ]:
                        print(f"Skipping {container.name}")
                        container_statuses.remove(container)

                main_container = pod.status.container_statuses[0].name

                if len(container_statuses) > 1:
                    click.echo("Multiple containers found.")
                    for i, container in enumerate(container_statuses):
                        click.echo(f"{i}: {container.name}")

                    while True:
                        i = click.prompt("Which is the main container?", type=int)
                        try:
                            main_container = pod.status.container_statuses[i].name
                            break
                        except IndexError:
                            click.echo("Invalid number, try again.")
                            continue

                click.echo(f"Streaming log from main container {main_container}")

                # At this point, our Pod has either begun running or finished.
                # For any case, we want to begin to tail (follow) logs from the
                # "main" container until the Pod exits. This works for both a
                # running Pod, and a finished Pod the same.

                # HACK: When _preload_context is passed, this causes the kubernetes
                # API here to return a urllib3 HTTPResponse object directly ready
                # to be streamed, so the following use of `resp.stream()` and
                # `resp.release_conn()` are just standard urllib3 behaviors.
                # This is entirely undocumented behavior afaik, and the only way to
                # actually get `follow=True` to work.
                resp = api.read_namespaced_pod_log(
                    namespace=namespace,
                    name=pod_name,
                    follow=True,
                    container=main_container,
                    _preload_content=False,
                )
                try:

                    def print_log_line(log_line):
                        print_line = True
                        if quiet and service_name == "topicctl":
                            print_line = all(
                                [
                                    "level=info" in log_line,
                                    "Processing topic" not in log_line,
                                    "Would create topic" not in log_line,
                                    "key(s)" not in log_line,
                                ]
                            )
                        if print_line:
                            click.echo(log_line, nl=False)

                    log_line = ""

                    # We need this, see
                    # https://urllib3.readthedocs.io/en/stable/user-guide.html#using-io-wrappers-with-response-content
                    resp.auto_close = False

                    # Use io.TextIOWrapper instead of resp.stream() since
                    # resp.stream() only wraps read(), which won't handle
                    # linebreak for us
                    for raw_line in io.TextIOWrapper(resp, encoding="unicode_escape"):
                        if service_name == "topicctl":
                            # new log entry, need to print out previous log entry
                            if raw_line.startswith("time="):
                                if log_line:
                                    print_log_line(log_line)
                                    log_line = ""

                            # append raw_line into current log entry, this is
                            # needed because pod log is not always \n separated.
                            log_line += raw_line
                        else:
                            print_log_line(raw_line)

                    if log_line:
                        print_log_line(log_line)
                        log_line = ""

                finally:
                    resp.release_conn()
                click.echo()
                read_logs = True
                # When finished reading logs, we need to loop back around
                # so we can query the final state of the Pod, in the case that
                # it was Running when we got here, we don't know if it succeeded
                # or failed.
    finally:
        click.echo(f"Deleting Job {namespace}/{name}")
        client.BatchV1Api(k8s_client).delete_namespaced_job(
            namespace=namespace, name=name, propagation_policy="Foreground"
        )
