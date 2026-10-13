.PHONY: all converter test

PYTHON ?= python3
CXX ?= c++
ROOT_CONFIG ?= root-config

all: converter
converter: bin/slim-root

bin/slim-root: src/slim_root.cpp src/muon.h
	mkdir -p bin
	$(CXX) -O3 -DNDEBUG -std=c++17 $$( $(ROOT_CONFIG) --cflags ) $< \
	  -o $@ $$( $(ROOT_CONFIG) --libs )

test: converter
	command -v root
	$(PYTHON) -m unittest discover -s tests -v
