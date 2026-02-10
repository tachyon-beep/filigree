.DEFAULT_GOAL := help
.PHONY: help install lint format typecheck test test-cov ci build clean

help:  ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

install:  ## Install dev dependencies
	uv sync --group dev

lint:  ## Run linter + format check
	uv run ruff check src/ tests/
	uv run ruff format --check src/ tests/

format:  ## Format code
	uv run ruff format src/ tests/
	uv run ruff check --fix src/ tests/

typecheck:  ## Run type checker
	uv run mypy src/filigree/

test:  ## Run tests
	uv run pytest

test-cov:  ## Run tests with coverage
	uv run pytest --cov --cov-report=term-missing --cov-fail-under=85

ci: lint typecheck test-cov  ## Run full CI locally (with coverage)

build:  ## Build sdist and wheel
	uv build

clean:  ## Remove build artifacts
	rm -rf dist/ build/ *.egg-info .mypy_cache .ruff_cache .pytest_cache .coverage
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
