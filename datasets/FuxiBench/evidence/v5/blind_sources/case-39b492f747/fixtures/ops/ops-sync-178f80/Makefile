PYTHON ?= python3

.PHONY: test tally clean

test:
	@PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -t . -q

tally:
	@PYTHONPATH=src $(PYTHON) -m vote_tally ballots

clean:
	@rm -f ballots/receipts.json
