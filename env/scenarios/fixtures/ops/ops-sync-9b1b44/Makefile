PYTHON ?= python3

.PHONY: test collect clean

test:
	@PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -t . -q

collect:
	@PYTHONPATH=src $(PYTHON) -m ops_sync.collect --out /tmp/ops-sync-9b1b44/collect

clean:
	@rm -rf /tmp/ops-sync-9b1b44/collect
