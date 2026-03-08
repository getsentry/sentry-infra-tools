from unittest.mock import patch, MagicMock

import pytest


def test_apply_uses_service_flag_when_cli_not_set():
    """When --server-side is not passed, _apply resolves from service flags."""
    from sentry_kube.cli.apply import _apply

    ctx = MagicMock()
    ctx.obj.customer_name = "test-region"
    ctx.obj.cluster_name = "default"
    ctx.obj.context_name = "test-context"
    ctx.obj.quiet_mode = False

    with (
        patch("sentry_kube.cli.apply._render", return_value=["apiVersion: v1\n"]),
        patch(
            "sentry_kube.cli.apply._diff_kubectl", return_value=(False, [])
        ) as mock_diff,
        patch(
            "sentry_kube.cli.apply.resolve_ssa_flags",
            return_value=(True, True),
        ) as mock_resolve,
    ):
        _apply(
            ctx=ctx,
            services=["my-service"],
            yes=True,
            filters=None,
            server_side=None,
            force_conflicts=None,
            important_diffs_only=False,
            allow_jobs=False,
            use_canary=False,
        )
        mock_resolve.assert_called_once_with(["my-service"], None, None)
        # _diff_kubectl should receive resolved True values
        mock_diff.assert_called_once()
        call_args = mock_diff.call_args
        assert call_args[0][2] is True  # server_side positional arg
        assert call_args[0][3] is True  # force_conflicts positional arg


def test_apply_passes_cli_flags_to_resolve():
    """When --server-side is explicitly set, it is passed to resolve_ssa_flags."""
    from sentry_kube.cli.apply import _apply

    ctx = MagicMock()
    ctx.obj.customer_name = "test-region"
    ctx.obj.cluster_name = "default"
    ctx.obj.context_name = "test-context"
    ctx.obj.quiet_mode = False

    with (
        patch("sentry_kube.cli.apply._render", return_value=["apiVersion: v1\n"]),
        patch("sentry_kube.cli.apply._diff_kubectl", return_value=(False, [])),
        patch(
            "sentry_kube.cli.apply.resolve_ssa_flags",
            return_value=(False, False),
        ) as mock_resolve,
    ):
        _apply(
            ctx=ctx,
            services=["my-service"],
            yes=True,
            filters=None,
            server_side=False,
            force_conflicts=False,
            important_diffs_only=False,
            allow_jobs=False,
            use_canary=False,
        )
        mock_resolve.assert_called_once_with(["my-service"], False, False)


def test_apply_conflict_raises():
    """When services have conflicting flags, _apply propagates the error."""
    import click
    from sentry_kube.cli.apply import _apply

    ctx = MagicMock()
    ctx.obj.customer_name = "test-region"
    ctx.obj.cluster_name = "default"

    with (
        patch(
            "sentry_kube.cli.apply.resolve_ssa_flags",
            side_effect=click.ClickException("conflicting"),
        ),
    ):
        with pytest.raises(click.ClickException, match="conflicting"):
            _apply(
                ctx=ctx,
                services=["svc-a", "svc-b"],
                yes=True,
                filters=None,
                server_side=None,
                force_conflicts=None,
                important_diffs_only=False,
                allow_jobs=False,
                use_canary=False,
            )


def test_apply_kubectl_uses_resolved_ssa_flags():
    """When SSA is resolved to True, kubectl apply gets --server-side."""
    from sentry_kube.cli.apply import _apply

    ctx = MagicMock()
    ctx.obj.customer_name = "test-region"
    ctx.obj.cluster_name = "default"
    ctx.obj.context_name = "test-context"
    ctx.obj.quiet_mode = False

    mock_process = MagicMock()
    mock_process.communicate.return_value = (b"", b"")

    with (
        patch("sentry_kube.cli.apply._render", return_value=["apiVersion: v1\n"]),
        patch(
            "sentry_kube.cli.apply._diff_kubectl", return_value=(True, ["+ something"])
        ),
        patch(
            "sentry_kube.cli.apply.resolve_ssa_flags",
            return_value=(True, True),
        ),
        patch(
            "sentry_kube.cli.apply.subprocess.Popen", return_value=mock_process
        ) as mock_popen,
        patch("sentry_kube.cli.apply.report_event_for_service_list"),
    ):
        _apply(
            ctx=ctx,
            services=["my-service"],
            yes=True,
            filters=None,
            server_side=None,
            force_conflicts=None,
            important_diffs_only=False,
            allow_jobs=False,
            use_canary=False,
        )
        apply_cmd = mock_popen.call_args[0][0]
        assert "--server-side" in apply_cmd
        assert "--force-conflicts" in apply_cmd
