## gr-dis GRC block installer
##
## Targets:
##   make install          pip install (editable) + GRC blocks
##   make install-python   pip install -e '.[dev]' only
##   make install-grc      copy *.block.yml to GRC_BLOCKS_DIR
##   make uninstall-grc    remove installed block definitions
##
## Run install-grc:
##   make install-grc
##
## Override the install path:
##   make install-grc GRC_BLOCKS_DIR=/usr/share/gnuradio/grc/blocks

GRC_BLOCKS_DIR ?= $(HOME)/.local/share/gnuradio/grc/blocks
GRC_SRCS       := $(wildcard grc/*.block.yml)

.PHONY: install install-python install-grc uninstall-grc

install: install-python install-grc

install-python:
	pip install -e ".[dev]"

install-grc: $(GRC_BLOCKS_DIR)
	cp $(GRC_SRCS) $(GRC_BLOCKS_DIR)/
	@echo "Installed $(words $(GRC_SRCS)) block definition(s) to $(GRC_BLOCKS_DIR)"

$(GRC_BLOCKS_DIR):
	mkdir -p $@

uninstall-grc:
	@for f in $(notdir $(GRC_SRCS)); do \
		echo "Removing $$f from $(GRC_BLOCKS_DIR)"; \
		rm -f "$(GRC_BLOCKS_DIR)/$$f"; \
	done
