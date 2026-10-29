PY ?= python3

.PHONY: test survey clean

test:
	@$(PY) -c "import tests; tests.selftest()"

survey:
	@$(PY) -c "import json; c = json.load(open('dns_targets.json')); print('targets:', len(c['targets']), 'qtype:', c['query_type'], 'timeout:', c['timeout_seconds'])"

clean:
	@rm -rf tests/__pycache__ .cache
