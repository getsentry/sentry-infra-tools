reset-python:
	pre-commit clean
	rm -rf .venv
.PHONY: reset-python

install-dev-dependencies:
	pip install -r requirements-dev.txt

install-all-dependencies: install-dev-dependencies
	pip install -r requirements.txt
.PHONY: install-python-dependencies

install-pre-commit-hook:
	pre-commit install --install-hooks
.PHONY: install-pre-commit-hook

install-brew-dev:
	brew bundle
.PHONY: install-brew-dev

develop: install-all-dependencies install-pre-commit-hook install-brew-dev

.PHONY: tools-test
tools-test:
	pytest -vv .

.PHONY: cli-typecheck
cli-typecheck:
	pip install -q -r requirements-dev.txt
	mypy config_builder --config-file mypy.ini --strict
