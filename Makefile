.PHONY: help check pre-commit test lint format css css-watch check-django check-migrations dev runserver migrate reclassify build-knn

.DEFAULT_GOAL := help

help: ## Show this help message
	@echo "Available commands:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

check: css lint check-django check-migrations test ## Run all pre-commit checks: CSS build, linters, formatters, migrations, and tests

pre-commit: check ## Alias for check

test: ## Run the full test suite with pytest
	uv run pytest

lint: ## Run pre-commit hooks (yaml, whitespace, ruff linter & formatter, uv.lock)
	uv run pre-commit run --all-files

format: ## Automatically fix and format code with ruff
	uv run ruff format .
	uv run ruff check --fix .

css: ## Compile and minify Tailwind CSS into protocol.css
	npm run build:css && printf '\n' >> core/static/css/protocol.css

css-watch: ## Watch and compile Tailwind CSS during frontend development
	npm run watch:css

check-django: ## Validate Django system configuration and models
	uv run modometa check

check-migrations: ## Check for missing or ungenerated database migrations
	uv run modometa makemigrations --check --dry-run

dev: ## Start local development server at http://127.0.0.1:8000
	uv run modometa runserver

runserver: dev ## Alias for dev

migrate: ## Apply database migrations
	uv run modometa migrate

reclassify: ## Reclassify tournament decks against latest archetype YAML rules
	uv run modometa reclassify_decks

build-knn: ## Rebuild the kNN deck similarity index (data/knn_index.npz)
	uv run modometa build_knn
