PY ?= python3
TARGETS ?= audit-targets.json

.PHONY: test survey clean

test:
	@$(PY) -m unittest discover -s tests -t . -v

survey:
	@$(PY) -c "import json;c=json.load(open('$(TARGETS)'));print('audit:',c['audit'],'stages:',len(c['stages']),'label_width:',c['label_width'])"

clean:
	@rm -rf tests/__pycache__ src/__pycache__ .cache
