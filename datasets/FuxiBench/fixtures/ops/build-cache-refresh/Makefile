.PHONY: build test clean

build:
	bash scripts/install_deps.sh
	python3 -m compileall -q src
	@echo "build ok"

test:
	python3 -m unittest discover -s tests -v

clean:
	rm -rf __pycache__ src/cache_report/__pycache__ tests/__pycache__
