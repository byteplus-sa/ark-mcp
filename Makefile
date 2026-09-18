# ============================================================================
# Ark Seed Multimodal MCP Server — task runner
#
# Wraps uv and fastmcp CLI commands for the Python MCP server.
# See plans/PLAN_ARK_SEED_MULTIMODAL_MCP.md for the full design.
#
# uv is the package manager and project runner.
# FastMCP is the MCP framework (decorator-based tools, resources, transports).
# This Makefile is the single entry point for common dev workflows.
# ============================================================================

SHELL := /bin/bash

# --- Tooling ---------------------------------------------------------------
UV      := uv
PYTHON  := python
FASTMCP := fastmcp
ENTRY   := src/ark_mcp/server.py:mcp

.DEFAULT_GOAL := help

.PHONY: help bootstrap install sync build dev start start-http shared-http test test-watch \
        lint typecheck format pre-commit-install pre-commit-run pre-commit-run-all \
        secrets-baseline audit inspect inspect-dev check-env clean setup

help: ## Show available targets
	@awk 'BEGIN {FS = ":.*##"; printf "Ark Seed MCP — task runner\n\nUsage:\n  make \033[36m<target>\033[0m\n\nTargets:\n"} /^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

# --- Setup -----------------------------------------------------------------

bootstrap: ## One-time scaffold: init uv project and add deps (Phase 1)
	$(UV) init --lib ark-mcp
	$(UV) add fastmcp httpx pydantic
	$(UV) add --dev ruff mypy pytest pytest-asyncio
	@echo "Bootstrap complete. Copy .env.example to .env and fill in credentials."

install: ## Create venv and install dependencies from uv.lock
	$(UV) sync

sync: install ## Alias for install (uv sync)

# --- Build & run -----------------------------------------------------------

build: ## Build the package into dist/
	$(UV) build

dev: ## Run server in dev mode over stdio with auto-reload
	$(FASTMCP) run $(ENTRY) --reload

start: ## Run the server over stdio
	$(FASTMCP) run $(ENTRY)

start-http: ## Run the server over Streamable HTTP (localhost:3000)
	MCP_TRANSPORT=http MCP_HOST=127.0.0.1 MCP_PORT=3000 $(UV) run python -m ark_mcp

shared-http: ## Run one shared loopback HTTP server (all local clients share jobs)
	MCP_AUTH_MODE=local MCP_TRANSPORT=http MCP_HOST=127.0.0.1 MCP_PORT=$${MCP_PORT:-3000} $(UV) run python -m ark_mcp

# --- Quality gates ---------------------------------------------------------

test: ## Run the offline test suite with coverage enforcement
	$(UV) run pytest --cov=ark_mcp --cov-report=term-missing

test-watch: ## Run tests in watch mode
	$(UV) run pytest-watch

lint: ## Lint with ruff
	$(UV) run ruff check src tests scripts
	$(UV) run ruff format --check src tests scripts

format: ## Format code with ruff
	$(UV) run ruff format src tests scripts

typecheck: ## Type-check with mypy
	$(UV) run mypy src

# --- Pre-commit ------------------------------------------------------------

pre-commit-install: ## Install pre-commit hooks into .git/hooks
	$(UV) run pre-commit install

pre-commit-run: ## Run pre-commit on staged files
	$(UV) run pre-commit run

pre-commit-run-all: ## Run pre-commit on all files
	$(UV) run pre-commit run --all-files

secrets-baseline: ## Regenerate .secrets.baseline from the current codebase
	$(UV) run detect-secrets scan > .secrets.baseline

audit: ## Audit dependencies for known vulnerabilities
	bash scripts/audit_dependencies.sh

# --- MCP inspection -------------------------------------------------------

inspect: ## Launch FastMCP inspector against the server
	$(FASTMCP) inspect $(ENTRY)

inspect-dev: ## Launch FastMCP inspector with auto-reload
	$(FASTMCP) inspect $(ENTRY) --reload

# --- Environment & maintenance --------------------------------------------

check-env: ## Validate required environment variables are set
	$(UV) run python -c "from ark_mcp.config.env import validate; validate()"

clean: ## Remove build artifacts and caches
	rm -rf dist build .ruff_cache .mypy_cache .pytest_cache .pre-commit-cache *.egg-info
	@echo "Cleaned build artifacts."

setup: install ## Full local setup: install dependencies
