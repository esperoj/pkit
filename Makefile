.DEFAULT_GOAL := help

PYTHON ?= python3
PIPX ?= pipx
VENV ?= .venv
BIN := $(VENV)/bin
PY := $(BIN)/python
COMPILE := $(PY) -m piptools compile --generate-hashes --strip-extras --allow-unsafe --no-emit-index-url

.PHONY: help
help: ## Show available make targets.
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z0-9_-]+:.*?##/ { printf "  %-15s %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

.PHONY: venv
venv: ## Create a local virtual environment.
	@test -d $(VENV) || $(PYTHON) -m venv $(VENV)

.PHONY: lock
lock: venv ## Lock runtime and dev dependencies with SHA-256 hashes.
	@$(PY) -m piptools --version >/dev/null 2>&1 || $(PY) -m pip install --upgrade pip "pip-tools>=7.6.2"
	$(COMPILE) -o requirements.txt pyproject.toml
	$(COMPILE) --extra=dev -o requirements-dev.txt pyproject.toml

.PHONY: upgrade
upgrade: venv ## Upgrade all locked dependencies to latest versions.
	@$(PY) -m piptools --version >/dev/null 2>&1 || $(PY) -m pip install --upgrade pip "pip-tools>=7.6.2"
	$(COMPILE) --upgrade -o requirements.txt pyproject.toml
	$(COMPILE) --upgrade --extra=dev -o requirements-dev.txt pyproject.toml

.PHONY: dev
dev: venv ## Install locked dev dependencies and editable package.
	@test -f requirements-dev.txt || { echo "error: requirements-dev.txt missing; run 'make lock' first."; exit 1; }
	$(PY) -m pip install --require-hashes -r requirements-dev.txt
	$(PY) -m pip install --no-deps -e .

.PHONY: install
install: ## Install pkit globally with pipx using locked runtime deps.
	@command -v $(PIPX) >/dev/null 2>&1 || { echo "error: pipx is required (https://pipx.pypa.io)."; exit 1; }
	@test -f requirements.txt || { echo "error: requirements.txt missing; run 'make lock' first."; exit 1; }
	PIPX_DEFAULT_BACKEND=pip $(PIPX) install --force .
	PIPX_DEFAULT_BACKEND=pip $(PIPX) runpip pkit install --require-hashes --no-deps --force-reinstall -r requirements.txt

.PHONY: uninstall
uninstall: ## Remove global pkit installation managed by pipx.
	@command -v $(PIPX) >/dev/null 2>&1 || { echo "error: pipx is required (https://pipx.pypa.io)."; exit 1; }
	$(PIPX) uninstall pkit

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
typecheck:
	$(BIN)/ty check

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
