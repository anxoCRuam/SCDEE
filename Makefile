# ============================================================
# SCDEE — Project-level Makefile
# ============================================================
# Convenience wrapper so developers don't have to `cd backend/`
# for every command. All targets delegate to docker-compose.
# ============================================================

COMPOSE = docker compose -f backend/docker-compose.yml
API_EXEC = $(COMPOSE) exec api
API_RUN = $(COMPOSE) run --rm api

.PHONY: help

# ── Docker lifecycle ─────────────────────────────────────────

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'
	@echo "\nMAIN:$(NC)"
	@echo "  make keygen     # Only needed if you don't have keys in .env file"
	@echo "  make build      # Build images"
	@echo "  make setup      # First time: configure everything"
	@echo "  make up         # Start services"
	@echo "  make check      # Run tests + lint + typecheck"
	@echo "  make reset      # Reset project (delete everything)"

setup: ## Initial setup (FIRST TIME)
	@make build
	@make up
	@sleep 5
	@make keygen
	@read -p "Done? (y/n): " confirm && [ "$$confirm" = "y" ]
	@make down
	@make build
	@make up
	@make makemigrations
	@make migrate
	@make createsuperuser
	@make sftp-watcher


# -- Docker lifecycle ------------------------------------------------
build: ## Build all containers
	$(COMPOSE) build

up: up-simple up-dev

up-simple:
	$(COMPOSE) up -d

up-dev:
	$(COMPOSE) --profile monitoring up -d

down: down-simple down-dev

down-simple:
	$(COMPOSE) down

down-dev:
	$(COMPOSE) --profile monitoring down

down-volumes: ## Stop all services and remove volumes (DANGER: deletes ALL data)
	$(COMPOSE) down -v

restart: ## Restart all services
	$(COMPOSE) restart

ps: ## Show running containers
	$(COMPOSE) ps

logs: ## Tail logs (all services)
	$(COMPOSE) logs -f

logs-api: ## Tail logs (API only)
	$(COMPOSE) logs -f api

logs-worker: ## Tail logs (Celery worker only)
	$(COMPOSE) logs -f celery_worker

logs-beat: ## Tail logs (Celery beat only)
	$(COMPOSE) logs -f celery_beat

logs-db: ## Tail logs (PostgreSQL only)
	$(COMPOSE) logs -f db

logs-redis: ## Tail logs (Redis only)
	$(COMPOSE) logs -f redis

logs-minio: ## Tail logs (MinIO only)
	$(COMPOSE) logs -f minio

# -- Django management -----------------------------------------------

migrate: makemigrations ## Run database migrations
	$(API_EXEC) python manage.py migrate

makemigrations: ## Generate new migrations
	$(API_EXEC) python manage.py makemigrations

sftp-watcher:
	$(API_EXEC) python manage.py sftp_watcher --poll-interval 5

reset-db: ## Reset database (DANGER: deletes ALL data)
	$(COMPOSE) down -v; \
	$(COMPOSE) up -d; \
	sleep 5; \
	$(API_EXEC) python manage.py migrate;

shell: ## Open Django shell
	$(API_EXEC) python manage.py shell

dbshell: ## Open PostgreSQL shell
	$(COMPOSE) exec db psql -U $${DB_USER:-scdee} -d $${DB_NAME:-scdee}

createsuperuser: ## Create a superadmin user
	$(API_EXEC) python manage.py createsuperuser

collectstatic: ## Collect static files (for Swagger UI etc.)
	$(API_EXEC) python manage.py collectstatic --noinput

# -- Quality & testing ----------------------------------------------

test: ## Run test suite
	$(API_EXEC) pytest

test-v: ## Run test suite (verbose)
	$(API_EXEC) pytest -v

test-parallel: ## Run test suite in parallel
	$(API_EXEC) pytest -n auto

test-cov: ## Run tests with coverage report
	$(API_EXEC) pytest --cov=apps --cov-report=term-missing --cov-report=html

lint: ## Run linter (ruff check)
	$(API_EXEC) ruff check .

format: ## Auto-format code (ruff format)
	$(API_EXEC) ruff format .

typecheck: ## Run type checker (mypy)
	$(API_EXEC) mypy .

check: ## Execute all checks (lint + typecheck + tests)
	@make lint
	@make typecheck
	@make test

# -- Keygen ----------------------------------------------------
keygen: ## Generate DJANGO_SECRET_KEY & ENCRYPTION_MASTER_KEY (run inside container)
	@echo "# ===================================="
	@echo "# Copy these into your .env file:"
	@$(API_EXEC) python -c \
		"from django.core.management.utils import get_random_secret_key; \
		 import secrets; \
		 print('DJANGO_SECRET_KEY=' + get_random_secret_key()); \
		 print('ENCRYPTION_MASTER_KEY=' + secrets.token_hex(32))"
	@echo "# ===================================="

# -- Create bucket ------------------------------------------------
setup-finish: ## Create the default MinIO bucket (requires api container up)
	$(API_EXEC) python manage.py setup_infrastructure

# -- Cleanup ---------------------------------------------------
prune: ## Clean up unused Docker resources (containers, images, volumes)
	docker system prune -f
	docker volume prune -f

clean: ## Completely clean up all Docker resources (DANGER: deletes ALL containers, images, volumes)
	$(COMPOSE) down --rmi all -v

destroy: ## Completely clean up all Docker resources (DANGER: deletes ALL containers, images, volumes)
	docker stop $(shell docker ps -aq) 2>/dev/null || true; \
	docker rm -f $(shell docker ps -aq) 2>/dev/null || true; \
	docker rmi -f $(shell docker images -q) 2>/dev/null || true; \
	docker volume rm -f $(shell docker volume ls -q) 2>/dev/null || true; \
	docker system prune -a --volumes -f;

reset: ## Reset entire project (stop containers, delete volumes, rebuild, and start fresh)
	@make down-volumes
	@make clean
	@make setup
