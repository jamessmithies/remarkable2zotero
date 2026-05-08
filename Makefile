VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
CLI := $(VENV)/bin/remarkable2zotero
ARGS ?=

.PHONY: setup list extract sync test lint clean

setup: $(VENV)/bin/activate

$(VENV)/bin/activate:
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"
	@echo "\n✓ Setup complete. Run 'make list' to test reMarkable connectivity."

list: $(VENV)/bin/activate
	$(CLI) list $(ARGS)

extract: $(VENV)/bin/activate
	$(CLI) extract $(ARGS)

sync: $(VENV)/bin/activate
	$(CLI) sync $(ARGS)

test: $(VENV)/bin/activate
	$(PYTHON) -m pytest tests/ $(ARGS)

lint: $(VENV)/bin/activate
	$(VENV)/bin/ruff check src/ tests/
	$(VENV)/bin/ruff format --check src/ tests/

clean:
	rm -rf $(VENV) build/ dist/ *.egg-info src/*.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +
