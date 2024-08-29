#!/bin/bash

die () { >&2 printf %s\\n "$1"; exit 1; }

[ -z "$VIRTUAL_ENV" ] && die "error: you don't seem to be in a python virtualenv."

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")"; pwd -P)"

cd "$HERE"
python -m ensurepip --upgrade
python -m pip install 'wheel==0.37.1' -r requirements.txt
python -m pip install -e . -e libsentrykube/

# --install-hooks is needed so that pre-commit adds this dir's config
# to the hook that it installs; we have multiple pre-commit configs
pre-commit install --install-hooks

if [[ "${SENTRY_KUBE_INSTALL_GIT_HOOKS:-}" != "0" ]]; then
    echo 'Installing git hooks.'
    cd "$(git rev-parse --show-toplevel)/.git/hooks"
    ln -sf git-hooks/* ./
fi
