from unittest.mock import patch

from click.testing import CliRunner

from sentry_kube.cli.argonaut import argonaut


def test_passes_args_through() -> None:
    with (
        patch("sentry_kube.cli.argonaut.shutil.which", return_value="/bin/argonaut"),
        patch("sentry_kube.cli.argonaut.subprocess.run") as run,
    ):
        result = CliRunner().invoke(argonaut, ["--theme", "dracula"])

    assert result.exit_code == 0
    run.assert_called_once_with(["argonaut", "--theme", "dracula"])


def test_missing_binary() -> None:
    with (
        patch("sentry_kube.cli.argonaut.shutil.which", return_value=None),
        patch("sentry_kube.cli.argonaut.subprocess.run") as run,
    ):
        result = CliRunner().invoke(argonaut, [])

    assert result.exit_code == 1
    assert "argonaut binary is missing" in result.output
    run.assert_not_called()
