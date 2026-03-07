from unittest.mock import patch, MagicMock

import pytest


def test_diff_uses_service_flag_when_cli_not_set():
    """When --server-side is not passed, _diff resolves from service flags."""
    from sentry_kube.cli.diff import _diff

    ctx = MagicMock()
    ctx.obj.customer_name = "test-region"
    ctx.obj.cluster_name = "default"
    ctx.obj.context_name = "test-context"

    with (
        patch("sentry_kube.cli.diff._render", return_value=["apiVersion: v1\n"]),
        patch(
            "sentry_kube.cli.diff._diff_kubectl", return_value=(False, [])
        ) as mock_kubectl,
        patch(
            "sentry_kube.cli.diff.resolve_ssa_flags",
            return_value=(True, True),
        ) as mock_resolve,
    ):
        _diff(
            ctx=ctx,
            services=["my-service"],
            filters=None,
            server_side=None,
            force_conflicts=None,
            important_diffs_only=False,
        )
        mock_resolve.assert_called_once_with(["my-service"], None, None)
        _, kwargs = mock_kubectl.call_args
        assert kwargs.get("server_side") is True or mock_kubectl.call_args[0][2] is True


def test_diff_passes_cli_flags_to_resolve():
    """When --server-side is explicitly set, it is passed to resolve_ssa_flags."""
    from sentry_kube.cli.diff import _diff

    ctx = MagicMock()
    ctx.obj.customer_name = "test-region"
    ctx.obj.cluster_name = "default"
    ctx.obj.context_name = "test-context"

    with (
        patch("sentry_kube.cli.diff._render", return_value=["apiVersion: v1\n"]),
        patch("sentry_kube.cli.diff._diff_kubectl", return_value=(False, [])),
        patch(
            "sentry_kube.cli.diff.resolve_ssa_flags",
            return_value=(True, False),
        ) as mock_resolve,
    ):
        _diff(
            ctx=ctx,
            services=["my-service"],
            filters=None,
            server_side=True,
            force_conflicts=False,
            important_diffs_only=False,
        )
        mock_resolve.assert_called_once_with(["my-service"], True, False)


def test_diff_conflict_raises():
    """When services have conflicting flags, _diff propagates the error."""
    import click
    from sentry_kube.cli.diff import _diff

    ctx = MagicMock()
    ctx.obj.customer_name = "test-region"
    ctx.obj.cluster_name = "default"

    with (
        patch(
            "sentry_kube.cli.diff.resolve_ssa_flags",
            side_effect=click.ClickException("conflicting"),
        ),
    ):
        with pytest.raises(click.ClickException, match="conflicting"):
            _diff(
                ctx=ctx,
                services=["svc-a", "svc-b"],
                filters=None,
                server_side=None,
                force_conflicts=None,
                important_diffs_only=False,
            )
