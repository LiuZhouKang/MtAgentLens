PYTHON ?= python3

.PHONY: all test qualify clean

all: test

test:
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -m unittest discover -s tests -t . -v

qualify: test

clean:
	rm -rf __pycache__ src/__pycache__ tests/__pycache__
