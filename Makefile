.PHONY: check converter test

PYTHON ?= $(HOME)/TOOLs/install/python-current/bin/python3
CXX ?= c++
ROOT_CONFIG ?= root-config

converter: bin/slim-root

bin/slim-root: src/slim_root.cpp
	mkdir -p bin
	$(CXX) -O3 -DNDEBUG -std=c++17 $$( $(ROOT_CONFIG) --cflags ) $< \
	  -o $@ $$( $(ROOT_CONFIG) --libs )

check:
	$(PYTHON) -m py_compile preserve.py tests/test_preserve.py
	$(PYTHON) preserve.py verify-catalog-backups
	bash -n scripts/build_remote.sh scripts/worker.sh

test: check
	$(PYTHON) -m unittest discover -s tests -v
