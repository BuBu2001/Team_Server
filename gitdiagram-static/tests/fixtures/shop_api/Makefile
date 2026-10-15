.PHONY: install test lint run
install:
	pip install -e ".[dev]"
test:
	pytest
lint:
	ruff check src
run:
	uvicorn shop.main:app --reload
