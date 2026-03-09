from unittest.mock import MagicMock, patch

import click
import pytest

from sentry_kube.cli.diff import _run_kubectl_diff


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


def _make_popen(returncode: int, stdout: bytes = b"", stderr: bytes = b""):
    proc = MagicMock()
    proc.communicate.return_value = (stdout, stderr)
    proc.returncode = returncode
    return proc


class TestRunKubectlDiff:
    @patch("sentry_kube.cli.diff.subprocess.Popen")
    def test_no_diffs(self, mock_popen):
        mock_popen.return_value = _make_popen(returncode=0)
        result = _run_kubectl_diff(["kubectl", "diff"], important_diffs_only=False)
        assert result == ""

    @patch("sentry_kube.cli.diff.subprocess.Popen")
    def test_diffs_found(self, mock_popen):
        diff_output = b"--- a/foo\n+++ b/foo\n-old\n+new\n"
        mock_popen.return_value = _make_popen(returncode=1, stdout=diff_output)
        result = _run_kubectl_diff(["kubectl", "diff"], important_diffs_only=False)
        assert result == diff_output.decode("utf-8")

    @patch("sentry_kube.cli.diff.subprocess.Popen")
    def test_diffs_found_with_stderr_warnings(self, mock_popen):
        """Exit code 1 with warnings on stderr should return diff output, not raise."""
        diff_output = b"--- a/foo\n+++ b/foo\n-old\n+new\n"
        warnings = b"Warning: Validation failed for ValidatingAdmissionPolicy\n"
        mock_popen.return_value = _make_popen(
            returncode=1, stdout=diff_output, stderr=warnings
        )
        result = _run_kubectl_diff(["kubectl", "diff"], important_diffs_only=False)
        assert result == diff_output.decode("utf-8")

    @patch("sentry_kube.cli.diff.subprocess.Popen")
    def test_no_diffs_with_stderr_warnings(self, mock_popen):
        """Exit code 0 with warnings on stderr should return normally."""
        warnings = b"Warning: some harmless warning\n"
        mock_popen.return_value = _make_popen(returncode=0, stdout=b"", stderr=warnings)
        result = _run_kubectl_diff(["kubectl", "diff"], important_diffs_only=False)
        assert result == ""

    @patch("sentry_kube.cli.diff.subprocess.Popen")
    def test_error_exit_code_raises(self, mock_popen):
        """Exit code >1 is a real error and should raise."""
        mock_popen.return_value = _make_popen(
            returncode=2, stderr=b"error: something went wrong\n"
        )
        with pytest.raises(click.ClickException, match="returned an error"):
            _run_kubectl_diff(["kubectl", "diff"], important_diffs_only=False)

    @patch("sentry_kube.cli.diff.subprocess.Popen")
    def test_error_exit_code_includes_stderr(self, mock_popen):
        error_text = b"error: the server could not find the requested resource\n"
        mock_popen.return_value = _make_popen(returncode=2, stderr=error_text)
        with pytest.raises(click.ClickException) as exc_info:
            _run_kubectl_diff(["kubectl", "diff"], important_diffs_only=False)
        assert "server could not find" in str(exc_info.value)

    @patch("sentry_kube.cli.diff.subprocess.Popen")
    def test_signal_killed_raises(self, mock_popen):
        """Negative return code (killed by signal) should raise."""
        mock_popen.return_value = _make_popen(returncode=-9, stderr=b"")
        with pytest.raises(click.ClickException, match="returned an error"):
            _run_kubectl_diff(["kubectl", "diff"], important_diffs_only=False)

    @patch("sentry_kube.cli.diff.subprocess.Popen")
    def test_segfault_raises(self, mock_popen):
        """SIGSEGV (-11) should raise."""
        mock_popen.return_value = _make_popen(
            returncode=-11, stderr=b"segmentation fault\n"
        )
        with pytest.raises(click.ClickException) as exc_info:
            _run_kubectl_diff(["kubectl", "diff"], important_diffs_only=False)
        assert "segmentation fault" in str(exc_info.value)
