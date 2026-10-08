.PHONY: test-mock test-backend test lint up

test-mock:
	cd mock-server && python -m pytest -q

test-backend:
	cd backend && python -m pytest -q

test: test-mock test-backend

lint:
	ruff check mock-server backend

up:
	docker compose up --build
