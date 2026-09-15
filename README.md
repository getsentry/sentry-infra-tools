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

`sentry-kube options` reads and makes incident-only changes directly to the
live `sentry-options` ConfigMaps. It does not invoke GoCD or GitHub Actions.
It discovers the relevant clusters from the current sentry-kube configuration:
all clusters running `getsentry`, plus the control-silo ConfigMap in both the
US and control clusters. Run it from the checkout that contains the fleet
configuration (normally `ops`), or pass that checkout with the global
`--root` option.

Use `get` to inspect ConfigMap values across the fleet. `<unset>` means the
new store does not declare the option; the application may therefore use a
legacy fallback or another configured default.

```shell
sentry-kube --root ~/dev/ops options get \
  --option billing.quota-enforcement
```

`set` is a dry run by default. It verifies `get` and `patch` access and parses
`values.json` in every selected ConfigMap before changing any cluster. It also
validates the requested key and strict JSON value with the native
`sentry_options.SchemaRegistry` used by the application. Point `--schemas` at
the Getsentry schema snapshot, or set `SENTRY_KUBE_OPTIONS_SCHEMAS` once for
the shell:

```shell
export SENTRY_KUBE_OPTIONS_SCHEMAS=~/dev/getsentry/sentry-options/schemas
```

The snapshot must be the revision deployed with the Getsentry image. Validation
proves the key and value are valid for that snapshot, but cannot prove that the
same schema revision is mounted by every running target. Pass `--schemas`
explicitly when the shell environment is not set. `--apply` is required to
make the change:

```shell
sentry-kube --root ~/dev/ops options set \
  --schemas ~/dev/getsentry/sentry-options/schemas \
  --option billing.quota-enforcement \
  --value false

sentry-kube --root ~/dev/ops options set \
  --schemas ~/dev/getsentry/sentry-options/schemas \
  --option billing.quota-enforcement \
  --value false \
  --apply
```

Use `--region` (configured names and aliases are accepted) or `--service`
(`getsentry` or `getsentry-control`) to restrict an invocation only when the
incident is intentionally scoped. Region selection is explicit:

```shell
# Include only US and DE. Repeat --region for every included region.
sentry-kube --root ~/dev/ops options get \
  --region us \
  --region de \
  --option billing.quota-enforcement

# Start with the full fleet and leave out single-tenant regions.
sentry-kube --root ~/dev/ops options set \
  --exclude-region geico \
  --exclude-region goldmansachs \
  --exclude-region ly \
  --option billing.quota-enforcement \
  --value false \
  --apply
```

`--region` and `--exclude-region` are mutually exclusive. Unknown regions fail
before any `kubectl` call. The default intentionally covers every configured
topology target rather than applying the generic `--stage` filter: the live
control-silo cluster is classified as `build` in the shared sentry-kube
configuration.

The tool uses a resource-version JSON Patch, so it refuses to overwrite a
ConfigMap changed after preflight. There is no cross-cluster transaction: a
patch failure after apply starts is reported with its exact cluster, and must
be retried after investigating that cluster. It validates strict JSON input,
the canonical `sentry-options` schema snapshot, and the deployed ConfigMap
structure (including both `generated_at` timestamps). It atomically refreshes
the ConfigMap annotation and the `values.json` timestamp.

The command confirms that it can prepare the ConfigMap write; it cannot prove
runtime precedence or pod reload. In particular, GetSentry currently has a
temporary dual-read rollout guard that continues to prefer a present legacy
option-store value. While that guard remains, a ConfigMap patch for such a key
is accepted but does not become the effective runtime value. Use the existing
legacy emergency procedure for those keys until the application rollout removes
that guard.

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
