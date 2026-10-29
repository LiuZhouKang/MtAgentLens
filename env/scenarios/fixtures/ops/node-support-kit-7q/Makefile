.PHONY: test diagnostics clean

test:
	@PYTHONPATH=src python3 -m unittest discover -s tests -t . -q

diagnostics:
	@bash scripts/build_support_bundle.sh

clean:
	@rm -rf build
