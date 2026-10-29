PY ?= python3
KNOWN_HOSTS ?= ssh/known_hosts

.PHONY: check test clean

check:
	$(PY) tools/check_known_hosts.py $(KNOWN_HOSTS)

test:
	$(PY) -m unittest discover -s tests -t . -v

clean:
	$(PY) -c "import shutil,sys; shutil.rmtree('__pycache__', ignore_errors=True); shutil.rmtree('tests/__pycache__', ignore_errors=True); shutil.rmtree('tools/__pycache__', ignore_errors=True)"
