PYTHON ?= python3
SERVICES := search-api auth-api pricing-api notify-api
ITERATIONS ?= 5
WARMUP ?= 1
OUT ?= /tmp/perf-bench-186dfe

.PHONY: all test bench bench-all budgets clean

all: test bench-all

test:
	@for s in $(SERVICES); do \
		$(PYTHON) -m unittest discover -s services/$$s -p 'test_*.py' -v || exit 1; \
	done

bench:
	@test -n "$(SERVICE)" || { echo "usage: make bench SERVICE=<service-name>"; exit 2; }
	$(PYTHON) bench/harness.py --service services/$(SERVICE) --iterations $(ITERATIONS) --warmup $(WARMUP) --out $(OUT)/$(SERVICE).json

bench-all:
	@for s in $(SERVICES); do \
		$(PYTHON) bench/harness.py --service services/$$s --iterations $(ITERATIONS) --warmup $(WARMUP) --out $(OUT)/$$s.json || exit 1; \
	done

budgets:
	$(PYTHON) bench/check_budgets.py --budgets bench/budgets.json $(OUT)/*.json

clean:
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
