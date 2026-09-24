.DEFAULT_GOAL := help

# Variables
PYTHON ?= python3
VENV := .venv
BIN := $(VENV)/bin
PY := $(BIN)/python
PIP := $(BIN)/pip

# Local bin directory for dev symlinks
LOCAL_BIN := bin

# Extract help from comments
.PHONY: help
help: ## Show this help message
	@echo "pkit monorepo development workflow"
	@echo ""
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z0-9_.-]+:.*?##/ { printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

# --- Setup & Dev Environment ---

.PHONY: dev
dev: dev-ocaml dev-python symlinks ## Setup full development environment

.PHONY: dev-ocaml
dev-ocaml: ## Install OCaml dependencies and build
	@echo "==> Installing OCaml dependencies..."
	opam install . --deps-only --with-test --yes
	@echo "==> Building OCaml project..."
	dune build

.PHONY: dev-python
dev-python: $(VENV)/touchfile ## Setup Python virtual environment and install deps

$(VENV)/touchfile: pyproject.toml
	@echo "==> Setting up Python virtual environment..."
	@test -d $(VENV) || $(PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"
	@touch $(VENV)/touchfile

.PHONY: symlinks
symlinks: build ## Create local BusyBox symlinks in ./bin/
	@echo "==> Creating local BusyBox symlinks..."
	@mkdir -p $(LOCAL_BIN)
	@cp _build/default/bin/pkit.exe $(LOCAL_BIN)/pkit
	@cd $(LOCAL_BIN) && ln -sf pkit wb && ln -sf pkit wayback
	@if [ -d "bash" ]; then \
		echo "==> Copying bash scripts..."; \
		for script in bash/*.sh; do \
			[ -f "$$script" ] && cp "$$script" $(LOCAL_BIN)/ && chmod +x $(LOCAL_BIN)/$$(basename "$$script"); \
		done \
	fi
	@echo "Local binaries ready in ./$(LOCAL_BIN)/ (Add to PATH: export PATH=\"$$PWD/bin:$$PATH\")"

# --- Build & Run ---

.PHONY: build
build: ## Build OCaml binaries
	dune build

.PHONY: run
run: build ## Run the main binary (usage: make run ARGS="wb save http://...")
	$(LOCAL_BIN)/pkit $(ARGS)

# --- Test & Quality ---

.PHONY: test
test: test-ocaml test-python ## Run all tests (OCaml and Python)

.PHONY: test-ocaml
test-ocaml: ## Run OCaml tests
	dune runtest

.PHONY: test-python
test-python: dev-python ## Run Python tests
	$(BIN)/pytest

.PHONY: fmt
fmt: ## Format all code (OCaml and Python)
	dune fmt
	@test -f $(BIN)/ruff && $(BIN)/ruff format . || echo "Python venv not setup, skipping ruff format."

.PHONY: lint
lint: ## Run linters (Python)
	@test -f $(BIN)/ruff && $(BIN)/ruff check . || echo "Python venv not setup, skipping ruff check."

.PHONY: check
check: fmt lint test ## Run full CI check locally (format, lint, test)

# --- Install (Global) ---

.PHONY: install
install: build ## Install globally (OCaml to opam switch, Python to pip) and create global symlinks
	@echo "==> Installing OCaml binaries to opam switch..."
	dune install
	@echo "==> Installing Python package..."
	@test -f $(BIN)/pip && $(BIN)/pip install . || pip install .
	@echo "==> Creating global BusyBox symlinks..."
	@PKIT_BIN=$$(command -v pkit 2>/dev/null); \
	if [ -n "$$PKIT_BIN" ]; then \
		DIR=$$(dirname "$$PKIT_BIN"); \
		ln -sf "$$PKIT_BIN" "$$DIR/wb"; \
		ln -sf "$$PKIT_BIN" "$$DIR/wayback"; \
		echo "Global symlinks created in $$DIR"; \
	else \
		echo "Warning: pkit binary not found in PATH after install."; \
	fi

# --- Clean ---

.PHONY: clean
clean: ## Clean build artifacts and local bin
	dune clean
	rm -rf $(LOCAL_BIN)
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete

.PHONY: distclean
distclean: clean ## Remove everything including virtual environment
	rm -rf $(VENV)
