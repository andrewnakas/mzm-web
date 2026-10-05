# Browser target for Metroid: Zero Mission.
#
# Builds mGBA's mCore to wasm plus a small shim, and emits a static page that
# takes the user's own ROM from a file picker. No ROM, and no bytes extracted
# from one, are produced or consumed by this Makefile.
#
#   make            build dist/ (the shippable page)
#   make test       also build the node-side core test harness
#   make selftest   run the muted core self-test against ~/decompfleet/roms
#   make browsercheck   run the real page in headless Chrome
#   make clean

MGBA      ?= $(CURDIR)/../ref/mgba
MGBA_BUILD = $(MGBA)/build-wasm
LIBMGBA    = $(MGBA_BUILD)/libmgba.a
ROMS      ?= $(HOME)/decompfleet/roms
ROM       ?= $(ROMS)/mzm.gba
SHOTS     ?=
EMSDK     ?= $(HOME)/decompfleet/shared/emsdk

# The shim reads struct mCore through mGBA's headers, so it MUST be compiled
# with the same -D set as libmgba.a or the fields land at different offsets and
# every vtable entry reads back as NULL — no link error, a null-function trap at
# the first call. Derived from the build rather than copied by hand, because
# hand-copying it is exactly how that bug gets in.
MGBA_DEFS = $(shell sed -n 's/^C_DEFINES = //p' $(MGBA_BUILD)/CMakeFiles/mgba.dir/flags.make)

CPPFLAGS = -I $(MGBA)/include -I $(MGBA)/src -I $(MGBA_BUILD)/include \
           -D_GNU_SOURCE -DPATH_MAX=4096 -DDISABLE_THREADING $(MGBA_DEFS)

# No audio device, no filesystem, no GL: the page owns the canvas and only
# creates an AudioContext if the user explicitly unmutes.
EMFLAGS = -s MODULARIZE=1 -s EXPORT_NAME=createGBA \
          -s ALLOW_MEMORY_GROWTH=1 -s INITIAL_MEMORY=64MB \
          -s EXPORTED_RUNTIME_METHODS='["ccall","cwrap","UTF8ToString","HEAPU8","HEAPU32","HEAP16"]' \
          -s EXPORTED_FUNCTIONS='["_malloc","_free"]' \
          -s FILESYSTEM=0

EMCC = $(EMSDK)/upstream/emscripten/emcc
NODE = $(EMSDK)/node/24.19.0_64bit/bin/node

.PHONY: all test selftest browsercheck check clean checklib symbols

all: dist/mzm.js dist/index.html

checklib:
	@test -f $(LIBMGBA) || { \
	  echo "missing $(LIBMGBA)"; \
	  echo "see ../STATUS.md for the exact emcmake line"; exit 1; }
	@test -n "$(MGBA_DEFS)" || { \
	  echo "could not read C_DEFINES from the mgba build; refusing to build with"; \
	  echo "mismatched struct layouts (every mCore function pointer would be NULL)"; exit 1; }

dist/mzm.js: src/shim.c $(LIBMGBA) Makefile | checklib
	@mkdir -p dist
	$(EMCC) -O3 $< $(LIBMGBA) $(CPPFLAGS) -o $@ $(EMFLAGS) -s ENVIRONMENT=web

dist/index.html: src/index.html
	@mkdir -p dist
	cp $< $@

test: test/core-node.js

test/core-node.js: src/shim.c $(LIBMGBA) Makefile | checklib
	@mkdir -p test
	$(EMCC) -O2 $< $(LIBMGBA) $(CPPFLAGS) -o $@ $(EMFLAGS) -s ENVIRONMENT=node

# Symbol addresses + struct offsets, derived from the matching build. Required
# by both test harnesses: "played" is gated on the game's own state.
symbols: gen/symbols.json

gen/symbols.json:
	@mkdir -p gen
	python3 ../tools/gen_symbols.py --out $@

# Core-level, outside a browser. Fast; isolates core bugs from page bugs.
selftest: test/core-node.js gen/symbols.json
	$(NODE) test/core-smoke.mjs $(ROM) gen/symbols.json

# The real page in headless Chrome, ROM fed through the actual file picker.
browsercheck: all gen/symbols.json
	python3 test/browser_check.py $(ROM) $(if $(SHOTS),--shots $(SHOTS),)

savecheck: all gen/symbols.json
	python3 ../tools/save_mechanism.py $(ROM)

check: selftest browsercheck savecheck

clean:
	rm -rf dist test/core-node.js test/core-node.wasm gen
