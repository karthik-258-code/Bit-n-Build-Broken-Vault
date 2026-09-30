# Convenience wrappers. Every target is a plain command that also works without make.
PYTHON ?= python
export PYTHONPATH := src

.PHONY: server test test-slow demo

server:
	$(PYTHON) -m brokenvault.server --data-dir ./vault --port 8765

test:
	$(PYTHON) -m pytest -q

test-slow:
	$(PYTHON) -m pytest -q -m slow

demo:
	$(PYTHON) scripts/demo.py
