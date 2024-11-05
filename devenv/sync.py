from __future__ import annotations

from devenv.lib import venv, gcloud, config
from devenv.constants import SYSTEM_MACHINE


def main(context: dict[str, str]) -> int:
    repo = context["repo"]
    reporoot = context["reporoot"]

    cfg = config.get_repo(reporoot)
    gcloud.install(
        cfg["gcloud"]["version"],
        cfg["gcloud"][SYSTEM_MACHINE],
        cfg["gcloud"][f"{SYSTEM_MACHINE}_sha256"],
        reporoot,
    )

    venv_dir, python_version, requirements, editable_paths, bins = venv.get(
        reporoot, repo
    )
    url, sha256 = config.get_python(reporoot, python_version)
    print(f"ensuring {repo} venv at {venv_dir}...")
    venv.ensure(venv_dir, python_version, url, sha256)

    print(f"syncing {repo} with {requirements}...")
    venv.sync(reporoot, venv_dir, requirements, editable_paths, bins)

    return 0
