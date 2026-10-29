.PHONY: test calibrate clean

test:
	@PYTHONPATH=src python3 -m unittest discover -s tests -t . -q

calibrate:
	@python3 scripts/record_calibration.py

clean:
	@rm -rf build .pytest_cache
