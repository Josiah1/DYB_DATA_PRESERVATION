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
	$(PYTHON) -m py_compile preserve.py scripts/build_catalog_backups.py tests/test_preserve.py tests/test_converter.py plots/residual_flasher/plot.py
	$(PYTHON) -m py_compile estimates/noenergy-2026-09-27/benchmark.py tests/test_benchmark.py
	$(PYTHON) -m py_compile tests/check_twin.py
	$(PYTHON) estimates/energy-threshold-2026-09-29/benchmark.py check
	$(PYTHON) preserve.py verify-catalog-backups
	bash -n scripts/build_remote.sh scripts/worker.sh plots/residual_flasher/collect.sh
	bash -n scripts/run_campaign.sh scripts/recover_campaign_disk.sh
	bash -n estimates/mini-branches-2026-09-27/compare-original.sh
	bash -n estimates/noenergy-2026-09-27/run.sh
	bash -n estimates/energy-threshold-2026-09-29/run.sh
	bash -n tests/check_twin.sh

test: check
	$(PYTHON) -m unittest discover -s tests -v
