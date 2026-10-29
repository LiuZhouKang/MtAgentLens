PYTHON ?= python3

.PHONY: test clean

test:
	@PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -t . -q

clean:
	@rm -rf build
