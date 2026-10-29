.PHONY: test clean

test:
	@FIXTURE_DIR="$${FIXTURE_DIR:-$(CURDIR)}" PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -t . -q

clean:
	@rm -rf build
