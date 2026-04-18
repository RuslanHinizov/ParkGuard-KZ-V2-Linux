# =============================================================================
# ParkGuard KZ — developer Makefile
# Usage:  make <target>     (list: make help)
# =============================================================================

SHELL := /bin/bash
.ONESHELL:
.DEFAULT_GOAL := help

# -------- Paths --------
PY_SERVICES       := violation-service plate-service event-api snapshot-consumer penalty-card-service worker-pool
PY_SERVICE_DIRS   := $(addprefix python-services/,$(PY_SERVICES))
SHARED_DIR        := python-services/shared
FRONTEND_DIR      := frontend
CPP_DIR           := cpp-deepstream
COMPOSE           := docker compose

# -------- Colors --------
C_RESET  := \033[0m
C_BOLD   := \033[1m
C_GREEN  := \033[32m
C_YELLOW := \033[33m

# =============================================================================
# Help
# =============================================================================
.PHONY: help
help:  ## show this help
	@echo -e "$(C_BOLD)ParkGuard KZ — make targets$(C_RESET)"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  $(C_GREEN)%-22s$(C_RESET) %s\n", $$1, $$2}'

# =============================================================================
# Infra
# =============================================================================
.PHONY: up down restart logs ps
up:           ## start all services (detached)
	$(COMPOSE) up -d

down:         ## stop all services (keep volumes)
	$(COMPOSE) down

restart:      ## restart all services
	$(COMPOSE) restart

logs:         ## tail logs (all services)
	$(COMPOSE) logs -f --tail=200

ps:           ## list running services
	$(COMPOSE) ps

infra-up:     ## start infra only (pg, redis, kafka, minio)
	$(COMPOSE) up -d postgres redis kafka minio

infra-down:   ## stop infra only
	$(COMPOSE) stop postgres redis kafka minio

# =============================================================================
# Database / Alembic
# =============================================================================
.PHONY: migrate migrate-new migrate-down seed
migrate:      ## run alembic upgrade head (inside event-api container)
	$(COMPOSE) run --rm event-api alembic upgrade head

migrate-new:  ## create new revision (NAME="msg")
	@test -n "$(NAME)" || (echo "Usage: make migrate-new NAME=\"description\"" && exit 1)
	$(COMPOSE) run --rm event-api alembic revision --autogenerate -m "$(NAME)"

migrate-down: ## rollback one migration
	$(COMPOSE) run --rm event-api alembic downgrade -1

seed:         ## load seed data (scripts/seed_db.sh)
	bash scripts/seed_db.sh

# =============================================================================
# Python tooling
# =============================================================================
.PHONY: lint typecheck test test-cov format
lint:         ## ruff check across all python services
	@for d in $(PY_SERVICE_DIRS) $(SHARED_DIR); do \
	  echo -e "$(C_YELLOW)→ ruff $$d$(C_RESET)"; \
	  ruff check $$d || exit 1; \
	done

typecheck:    ## mypy --strict across all python services
	@for d in $(PY_SERVICE_DIRS) $(SHARED_DIR); do \
	  echo -e "$(C_YELLOW)→ mypy $$d$(C_RESET)"; \
	  mypy --strict $$d || exit 1; \
	done

test:         ## pytest all python services
	@for d in $(PY_SERVICE_DIRS); do \
	  if [ -d "$$d/tests" ]; then \
	    echo -e "$(C_YELLOW)→ pytest $$d$(C_RESET)"; \
	    pytest $$d/tests -v || exit 1; \
	  fi; \
	done

test-cov:     ## pytest with coverage (≥80% per spec §18)
	pytest --cov=python-services --cov-report=term-missing --cov-fail-under=80

format:       ## ruff format
	@for d in $(PY_SERVICE_DIRS) $(SHARED_DIR); do \
	  ruff format $$d; \
	done

# =============================================================================
# C++ DeepStream
# =============================================================================
.PHONY: cpp-build cpp-clean cpp-run
cpp-build:    ## build C++ deepstream-app (Ubuntu host)
	cd $(CPP_DIR) && cmake -B build -S . -DCMAKE_BUILD_TYPE=Release && cmake --build build -j$$(nproc)

cpp-clean:    ## clean C++ build dir
	rm -rf $(CPP_DIR)/build

cpp-run:      ## run deepstream-app in container
	$(COMPOSE) up -d deepstream-app
	$(COMPOSE) logs -f deepstream-app

# =============================================================================
# Frontend
# =============================================================================
.PHONY: frontend-install frontend-dev frontend-build frontend-lint
frontend-install:  ## npm install
	cd $(FRONTEND_DIR) && npm install

frontend-dev:      ## vite dev server
	cd $(FRONTEND_DIR) && npm run dev

frontend-build:    ## production build
	cd $(FRONTEND_DIR) && npm run build

frontend-lint:     ## eslint + tsc --noEmit
	cd $(FRONTEND_DIR) && npm run lint && npx tsc --noEmit

# =============================================================================
# Models / engines (host build, NOT in container — Blackwell)
# =============================================================================
.PHONY: engines nomeroff
engines:      ## build TensorRT engines on host (RTX 5070 Ti)
	bash scripts/build_tensorrt_engines.sh

nomeroff:     ## download Nomeroff-net models
	bash scripts/download_nomeroff_models.sh

# =============================================================================
# Smoke / health
# =============================================================================
.PHONY: smoke health
smoke:        ## run smoke test (scripts/smoke_test.sh)
	bash scripts/smoke_test.sh

health:       ## hit /healthz on each service
	@curl -sf http://localhost:8000/healthz && echo " event-api OK"
	@curl -sf http://localhost:9100/healthz && echo " deepstream OK"

# =============================================================================
# Cleanup
# =============================================================================
.PHONY: clean clean-all
clean:        ## remove python caches, node_modules, build dirs
	find . -type d \( -name __pycache__ -o -name .pytest_cache -o -name .ruff_cache -o -name .mypy_cache \) -exec rm -rf {} + 2>/dev/null || true
	rm -rf $(CPP_DIR)/build $(FRONTEND_DIR)/dist $(FRONTEND_DIR)/node_modules

clean-all:    ## clean + docker volumes (DESTRUCTIVE — confirms)
	@read -p "This will delete docker volumes (DB data, Redis state). Continue? [y/N] " ans && [ "$$ans" = "y" ]
	$(COMPOSE) down -v
	$(MAKE) clean
