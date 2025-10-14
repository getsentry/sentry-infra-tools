# sentry-kube
# -C us
# run-pod
# --exec
# -it
# --deployment kafkactl
# --service kafkactl
# --container kafkactl
# --keep-args
# -- bash
import click

__all__ = ("kafkactl",)


@click.command(help="Spawn kafkactl environment pod.")
@click.pass_context
def kafkactl(ctx):
    from click import Context

    from sentry_kube.cli.run_pod import run_pod

    run_pod_ctx = Context(
        run_pod,
        parent=ctx,
        info_name="kafkactl",
        obj=ctx.obj,
        allow_extra_args=True,
    )

    run_pod_ctx.args = ["bash"]

    run_pod_ctx.invoke(
        run_pod.callback,
        deployment="kafkactl",
        service="kafkactl",
        container="kafkactl",
        keep_args=True,
        exec_=True,
        tty=True,
        no_security_context=False,
        interactive=True,
        namespace="sentry-system",
        version=None,
        command=None,
        args=None,
        clear_labels=False,
        delete=False,
        only_delete=False,
        selective_delete=None,
        root=False,
        safe_to_evict=False,
        memory=None,
    )
    run_pod_ctx.invoke(
        run_pod.callback,
        deployment="kafkactl",
        service="kafkactl",
        container="kafkactl",
        keep_args=True,
        exec_=True,
        tty=True,
        no_security_context=False,
        interactive=True,
        namespace="sentry-system",
        version=None,
        command=None,
        args=None,
        clear_labels=False,
        delete=False,
        only_delete=True,
        selective_delete=None,
        root=False,
        safe_to_evict=False,
        memory=None,
    )
