# Convenience targets for dataizen-ckan.
# Run `make help` to list available targets.

CKAN_URL ?= http://localhost:8080
PYTHON   ?= python3

.PHONY: help build up down logs ps \
        test-unit test-e2e test-all \
        test-e2e-install ckan-ready

help:
	@echo "Targets:"
	@echo "  build            - docker compose build (ckan, mapserver, db)"
	@echo "  up               - start stack (./start-local.sh)"
	@echo "  up-init          - start stack + erase DB + init (./start-local.sh --init)"
	@echo "  down             - docker compose down"
	@echo "  ps / logs        - docker compose ps / logs -f"
	@echo "  ckan-ready       - wait until CKAN responds on $(CKAN_URL)"
	@echo ""
	@echo "  test-unit        - run unit tests of ckanext-ogc (in-container)"
	@echo "  test-e2e         - run end-to-end ingestion suite against the local stack"
	@echo "  test-e2e-install - install pytest+requests+pyshp for E2E"
	@echo "  test-all         - test-unit then test-e2e"

build:
	docker compose build ckan mapserver db

up:
	./start-local.sh

up-init:
	./start-local.sh --init

down:
	docker compose down

ps:
	docker compose ps

logs:
	docker compose logs -f --tail=200

ckan-ready:
	@echo "Waiting for CKAN at $(CKAN_URL) ..."
	@for i in $$(seq 1 60); do \
	  code=$$(curl -sS -o /dev/null -w "%{http_code}" $(CKAN_URL)/api/3/action/status_show || echo 000); \
	  if [ "$$code" = "200" ]; then echo "CKAN ready"; exit 0; fi; \
	  sleep 2; \
	done; \
	echo "CKAN not ready after 120s on $(CKAN_URL)"; exit 1

test-unit:
	docker compose exec -T ckan bash -lc "cd /srv/app/ckanext-ogc && pytest -q"

test-e2e-install:
	$(PYTHON) -m pip install -r tests-e2e/requirements.txt

test-e2e: ckan-ready
	cd tests-e2e && $(PYTHON) -m pytest

test-all: test-unit test-e2e
