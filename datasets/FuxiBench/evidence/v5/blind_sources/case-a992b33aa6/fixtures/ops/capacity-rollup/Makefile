.PHONY: test build clean

test:
	@python3 -I tests/test_rollup.py
	@python3 -I tests/run_checks.py

build:
	@python3 -I -c "import ast, pathlib; ast.parse(pathlib.Path('src/rollup.py').read_text())"

clean:
	@rm -rf __pycache__ tests/__pycache__
