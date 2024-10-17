# `sentry-kube`

```
   __                  __
  /  |                /  |
  $$ |   __  __    __ $$ |____    ______
  $$ |  /  |/  |  /  |$$      \  /      \
  $$ |_/$$/ $$ |  $$ |$$$$$$$  |/$$$$$$  |
  $$   $$<  $$ |  $$ |$$ |  $$ |$$    $$ |
  $$$$$$  \ $$ \__$$ |$$ |__$$ |$$$$$$$$/
  $$ | $$  |$$    $$/ $$    $$/ $$       |
  $$/   $$/  $$$$$$/  $$$$$$$/   $$$$$$$/

  Get kubed.
```

## Installation

Follow the setup instructions in the [top-level README](../../README.md).

## Help

All commands support `--help`, so please reference this.

```shell
sentry-kube --help
```

## Environment Variables

`sentry-kube` can be further configured by setting environment variables.

* `SENTRY_KUBE_CONFIG_FILE`: Set this to the full path of the configuration file that contains the clusters and customers configuration for sentry-kube. It defaults to `[workspace_root]/cli_config/configuration.yaml`
* `SENTRY_KUBE_ENABLE_NOTIFICATIONS`: Set `SENTRY_KUBE_ENABLE_NOTIFICATIONS=1` to enable MacOS notifications for things like `sentry-kube connect` bastion connections
* `SENTRY_KUBE_KUBECTL_DIFF_CONCURRENCY`: Set `SENTRY_KUBE_KUBECTL_DIFF_CONCURRENCY` to make `kubectl diff` process objects in parallel
* `SENTRY_KUBE_IAP`: Access Kubernetes API through Google Identity-Aware Proxy and a jump host instead of standard bastion and sshuttle.
* `SENTRY_KUBE_KUBECTL_VERSION`: Set `SENTRY_KUBE_KUBECTL_VERSION=1.22.17` to configure the kubectl version to use
* `SENTRY_KUBE_NO_CONTEXT`: Set `SENTRY_KUBE_NO_CONTEXT=1` to skip checking for a functional kube context
* `SENTRY_KUBE_ROOT`: Sets the workspace root. It defaults to the git root directory.

## How to use sentry-infra-tools in editable mode (for development) in another environment

Lets assume you have a local working copy of sentry-infra-tools in
`<path-to-local-working-copy>/sentry-infra-tools`. Lets assume that you made some change
in your local copy of sentry-infra-tools. But you would like to validate
the change in a different virtual environment. Here is how you can do it:

1. Remove the existing sentry-infra-tools package from the environment
   where you want to test it out.

```shell
pip uninstall sentry-infra-tools
```

2. Install the local working copy of sentry-infra-tools in editable mode. You can do this either manually as shown below.

```shell
pip install -e <path-to-local-working-copy>/sentry-infra-tools
```

Or if `requirements.txt` is being used, you can remove the existing reference to `sentry-infra-tools` and add a reference to the local working copy.

```shell
# Edit python/requirements.txt
# Remove any existing reference to sentry-infra-tools
# Add the following reference to local working copy
-e <path-to-local-working-copy>/sentry-infra-tools
```

and then run `pip install -r requirements.txt`.

3. Done. You should now be able to use the local working copy of sentry-infra-tools in the other environment.
