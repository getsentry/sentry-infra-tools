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

Run `./install.sh` to install sentry-kube.

## Releasing a new version

Versioning note: When cutting a new release we should try to follow [SemVer](https://semver.org/).

To cut a new release, we use the `Release` Github Actions Workflow. This can be triggered manually using the [UI](https://github.com/getsentry/sentry-infra-tools/actions/workflows/release.yml)

![image](https://github.com/user-attachments/assets/96fc8c19-4855-4258-8565-c959317d9723)

Or with the [`gh`](https://cli.github.com) CLI:

```
gh workflow run Release --field version=0.0.33
```

## Help

All commands support `--help`, so please reference this.

```shell
sentry-kube --help
```

## Emergency sentry-options changes

`sentry-kube break-glass set` makes an incident-only change directly to the
live `sentry-options` ConfigMaps. It does not invoke GoCD or GitHub Actions.
It discovers the relevant clusters from the current sentry-kube configuration:
all clusters running `getsentry`, plus the control-silo ConfigMap in both the
US and control clusters.

The command is a dry run by default. It verifies `get` and `patch` access and
parses `values.json` in every selected ConfigMap before changing any cluster.
Pass JSON to `--value`; `--apply` is required to make the change:

```shell
sentry-kube break-glass set \
  --option billing.quota-enforcement \
  --value false

sentry-kube break-glass set \
  --option billing.quota-enforcement \
  --value false \
  --apply
```

Use `--region` or `--configmap-target` to restrict an invocation only when the
incident is intentionally scoped. The tool uses a resource-version JSON Patch,
so it refuses to overwrite a ConfigMap changed after preflight. There is no
cross-cluster transaction: a patch failure after apply starts is reported with
its exact cluster, and must be retried after investigating that cluster.

This is intentionally temporary. The next normal `sentry-options-automator`
deployment restores the declarative value from `option-values/`; make the
corresponding normal change if the emergency value should remain in effect.

## Environment Variables

`sentry-kube` can be further configured by setting environment variables.

* `SENTRY_KUBE_CONFIG_FILE`: Set this to the full path of the configuration file that contains the clusters and customers configuration for sentry-kube. It defaults to `[workspace_root]/cli_config/configuration.yaml`
* `SENTRY_KUBE_ENABLE_NOTIFICATIONS`: Set `SENTRY_KUBE_ENABLE_NOTIFICATIONS=1` to enable MacOS notifications for things like `sentry-kube connect` bastion connections
* `SENTRY_KUBE_KUBECTL_DIFF_CONCURRENCY`: Set `SENTRY_KUBE_KUBECTL_DIFF_CONCURRENCY` to make `kubectl diff` process objects in parallel
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
