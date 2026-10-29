PYTHON ?= python3

.PHONY: test inventory

test:
	@PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -t . -q

inventory:
	@PYTHONPATH=src $(PYTHON) -c "import inventory; print(inventory.SCHEMA)"
