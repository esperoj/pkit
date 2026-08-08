.DEFAULT_GOAL := help

PYTHON ?= python3
VENV ?= .venv
BIN := $(VENV)/bin
PY := $(BIN)/python
PIP := $(BIN)/pip

.PHONY: help
help: ## Show available make targets.
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z0-9_-]+:.*?##/ { printf "  %-15s %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

.PHONY: venv
venv: ## Create a local virtual environment.
	@test -d $(VENV) || $(PYTHON) -m venv $(VENV)

.PHONY: dev
dev: venv ## Install project and development dependencies.
	$(PIP) install -e ".[dev]"

.PHONY: install
install: dev ## Alias for make dev.

.PHONY: test
test: ## Run tests.
	$(PY) -m pytest

.PHONY: coverage
coverage: ## Run tests with coverage.
	$(PY) -m pytest --cov=pkit --cov-report=term-missing

.PHONY: lint
lint: ## Run ruff lint checks.
	$(BIN)/ruff check .

.PHONY: lint-fix
lint-fix: ## Run safe ruff lint fixes.
	$(BIN)/ruff check --fix .

.PHONY: format
format: ## Format code with ruff.
	$(BIN)/ruff format .

.PHONY: format-check
format-check: ## Check formatting without changing files.
	$(BIN)/ruff format --check .

.PHONY: typecheck
typecheck: ## Run pyright static type checking.
	$(BIN)/pyright

.PHONY: check
check: format-check lint typecheck coverage ## Run full local check.

.PHONY: fix
fix: lint-fix format ## Apply safe lint fixes and formatting.

.PHONY: clean
clean: ## Remove caches and build artifacts.
	rm -rf .pytest_cache .ruff_cache .coverage coverage.xml htmlcov build dist *.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete

.PHONY: distclean
distclean: clean ## Remove caches, build artifacts, and the virtual environment.
	rm -rf $(VENV)
