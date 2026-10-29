.PHONY: test replay clean

test:
	@PYTHONPATH=src python3 -m unittest discover -s tests -t . -q

replay:
	@PYTHONPATH=src python3 -m session_audit.replay

clean:
	@rm -rf build __pycache__ src/__pycache__ src/session_audit/__pycache__ tests/__pycache__
