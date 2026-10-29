PYTHON ?= python3
SERVICE := billing-service

.PHONY: all build test clean

all: build test

build:
	@mkdir -p build
	$(PYTHON) -m compileall -q src
	@printf 'service=%s\nbuilt_at=%s\n' "$(SERVICE)" "$$(date -u +%Y-%m-%dT%H:%M:%SZ)" > build/$(SERVICE).artifact
	@echo "build: wrote build/$(SERVICE).artifact"

test:
	$(PYTHON) -m unittest discover -s tests -v

clean:
	rm -rf build
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
