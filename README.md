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

## Environment Variables

`sentry-kube` can be further configured by setting environment variables.

* `SENTRY_KUBE_CONFIG_FILE`: Set this to the full path of the configuration file that contains the clusters and customers configuration for sentry-kube. It defaults to `[workspace_root]/cli_config/configuration.yaml`
* `SENTRY_KUBE_ENABLE_NOTIFICATIONS`: Set `SENTRY_KUBE_ENABLE_NOTIFICATIONS=1` to enable MacOS notifications for things like `sentry-kube connect` bastion connections
* `SENTRY_KUBE_HELM_API_VERSIONS`: Comma-separated list of extra `Capabilities.APIVersions` passed to `helm template` when materializing helm manifests
* `SENTRY_KUBE_HELM_CHART_CACHE`: Directory where remote helm charts pulled at pinned versions are cached. Defaults to `~/.cache/sentry-kube/helm-charts`
* `SENTRY_KUBE_HELM_KUBE_VERSION`: Kubernetes version passed to `helm template --kube-version` when materializing helm manifests
* `SENTRY_KUBE_KUBECTL_DIFF_CONCURRENCY`: Set `SENTRY_KUBE_KUBECTL_DIFF_CONCURRENCY` to make `kubectl diff` process objects in parallel
* `SENTRY_KUBE_KUBECTL_VERSION`: Set `SENTRY_KUBE_KUBECTL_VERSION=1.22.17` to configure the kubectl version to use
* `SENTRY_KUBE_NO_CONTEXT`: Set `SENTRY_KUBE_NO_CONTEXT=1` to skip checking for a functional kube context
* `SENTRY_KUBE_ROOT`: Sets the workspace root. It defaults to the git root directory.

## Materializing helm manifests

Non-helm services have always been rendered into reviewable manifests under
`materialized_manifests`. Helm services get the same treatment with:

```shell
# CI-style: re-render every helm service affected by the given changed files
python -m sentry_kube.render_helm_manifests <changed files...>

# Locally, for one region/cluster
sentry-kube -C <region> helm render --materialize-manifests --all
```

This runs an offline `helm template` per release, using the pinned chart
version from `_helm.yaml` and the same merged values that
`render_helm_services` writes to `materialized_helm_values` (which is left
unchanged). The output goes to a parallel `materialized_helm_manifests/`
tree — `{cluster}/{service}/[{release}/]{namespace}-{kind}-{name}.yaml` —
kept separate from `materialized_manifests` so visibility-only output is
never confused with sentry-kube apply paths.

The render is deterministic: charts are fetched at their exact pinned
version into a content-addressed cache, `--kube-version`/`--api-versions`
are pinned explicitly (see the environment variables above), and re-running
with no input changes produces a byte-identical tree. A chart that fails to
fetch or render fails the run loudly rather than being skipped.

Caveats:

* Charts are rendered without cluster access, so `lookup()` calls return
  empty results (standard `helm template` behavior) and
  capabilities-dependent logic only sees what `--api-versions` declares.
  Audit a chart with `grep -r "lookup(" <chart>/templates` and treat its
  rendered output as best-effort.
* Dynamic app versions (`dynamic_app_version`) and blue/green active flags
  normally come from the live release; the offline render keeps whatever
  the chart defaults and merged values define instead.
* Charts that generate content at template time (`randAlphaNum` secrets,
  `now` timestamps) produce different output on every render. Pin such
  values explicitly in the merged values to keep the tree stable.
* Pulling charts from OCI repositories (`oci://...`) requires registry
  credentials (`helm registry login` / `gcloud auth configure-docker`);
  plain https chart repositories need no authentication.

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
