PY ?= python3
CONFIG ?= ssh/config
KNOWN_HOSTS ?= ssh/known_hosts

.PHONY: check test clean

check:
	$(PY) tools/inspect_ssh.py $(CONFIG) $(KNOWN_HOSTS)

test:
	$(PY) -m unittest discover -s tests -t . -v

clean:
	$(PY) -c "import shutil; [shutil.rmtree(p, ignore_errors=True) for p in ('__pycache__', 'tests/__pycache__', 'tools/__pycache__')]"
