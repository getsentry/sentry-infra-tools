reset-python:
	pre-commit clean
	rm -rf .venv
.PHONY: reset-python

install-python-dependencies:
	pip install -q -r requirements-dev.txt
	pip install -q -r requirements.txt
	pip install -q pre-commit==2.13.0
.PHONY: install-python-dependencies

install-pre-commit-hook:
	pre-commit install --install-hooks
.PHONY: install-pre-commit-hook

install-brew-dev:
	brew bundle
.PHONY: install-brew-dev

develop: install-python-dependencies install-pre-commit-hook install-brew-dev

.PHONY: tools-test
tools-test:
	pytest -vv .

.PHONY: cli-typecheck
cli-typecheck:
	pip install -q -r .requirements-dev.txt
	mypy config_builder --config-file k8s/mypy.ini --strict
