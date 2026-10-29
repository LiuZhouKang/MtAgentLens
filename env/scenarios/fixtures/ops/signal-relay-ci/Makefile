.PHONY: build deps test clean

build: deps
	@python3 src/main.py --selftest
	@printf 'build complete\n'

deps:
	@bash scripts/install_deps.sh

test:
	@python3 -m unittest discover -s tests -q
	@python3 src/main.py --verify

clean:
	@rm -rf build
