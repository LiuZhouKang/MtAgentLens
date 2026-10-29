PYTHON ?= python3

.PHONY: all test aggregate clean

all: test

test:
	$(PYTHON) -m unittest discover -s tests -v

aggregate:
	$(PYTHON) pipeline/aggregate.py aggregate --shards shards --out build

clean:
	rm -rf build
