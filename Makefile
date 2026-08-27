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

.PHONY: bump
bump: ## Set the project version. Usage: make bump VERSION=x.y.z
	$(if $(VERSION),,$(error usage: make bump VERSION=x.y.z))
	@echo "$(VERSION)" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+([.-]?[0-9A-Za-z][0-9A-Za-z.-]*)?$$' || { echo "error: VERSION must look like x.y.z"; exit 2; }
	@sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | grep -q . || { echo "error: no version field found in pyproject.toml"; exit 1; }
	sed -i 's/^version = ".*"/version = "$(VERSION)"/' pyproject.toml
	@echo "pyproject.toml version set to $(VERSION)"

.PHONY: release
release: ## Bump VERSION, run full check, commit, and tag. Usage: make release VERSION=x.y.z
	$(if $(VERSION),,$(error usage: make release VERSION=x.y.z))
	@command -v git >/dev/null 2>&1 || { echo "error: git is required for release."; exit 1; }
	@test "$$(git rev-parse --abbrev-ref HEAD)" = "main" || { echo "error: release from the main branch."; exit 1; }
	@git diff --quiet && git diff --cached --quiet || { echo "error: working tree is not clean; commit or stash changes first."; exit 1; }
	$(MAKE) dev
	$(MAKE) bump VERSION=$(VERSION)
	$(MAKE) check
	git add pyproject.toml
	git commit -m "Release v$(VERSION)"
	git tag -a "v$(VERSION)" -m "v$(VERSION)"
	@echo "Tagged v$(VERSION). Push with: git push origin main v$(VERSION)"
.PHONY: setup-remotes

setup-remotes:
	@git config --remove-section remote.origin
	@git remote add origin git@github.com:esperoj/pkit.git
	@git remote set-url --push origin git@github.com:esperoj/pkit.git
	@git remote set-url --add --push origin git@codefloe.com:esperoj/pkit.git
	@echo "Remotes configured:"
	@git remote -v

.PHONY: clean
clean: ## Remove caches and build artifacts.
	rm -rf .pytest_cache .ruff_cache .coverage coverage.xml htmlcov build dist *.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete

.PHONY: distclean
distclean: clean ## Remove caches, build artifacts, and the virtual environment.
	rm -rf $(VENV)
