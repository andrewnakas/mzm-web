# mzm — Metroid: Zero Mission

**The decomp builds byte-identical to the staged USA ROM**, with the compiler the
project actually uses (not the fleet's shared agbcc — see §2). The browser target
runs that ROM on an emscripten build of mGBA, from a file the user picks
themselves, and the muted test plays the game far enough to move Samus around
Brinstar with the game's own state confirming it is play and not an attract demo.

---

## 1. The matching build

Upstream documents its expected hashes in `README.md` and in `mzm_us.sha1`. I
checked those against the staged ROM **before** building anything. They agree
exactly — there is no mismatch to report.

| | sha1 | md5 | size |
|---|---|---|---|
| staged `roms/mzm.gba` | `5de8536afe1f0078ee6fe1089f890e8c7aa0a6e8` | `ebbce58109988b6da61ebb06c7a432d5` | 8388608 |
| upstream `mzm_us.sha1` | `5de8536afe1f0078ee6fe1089f890e8c7aa0a6e8` | — | — |
| upstream `README.md` | `5de8536afe1f0078ee6fe1089f890e8c7aa0a6e8` | — | — |
| **built `mzm_us.gba`** | **`5de8536afe1f0078ee6fe1089f890e8c7aa0a6e8`** | `ebbce58109988b6da61ebb06c7a432d5` | 8388608 |

Cartridge header of the build: title `ZEROMISSIONE`, game code `BMXE`, maker `01`,
software version byte (0xBC) `0`. Save media signature in the ROM: `SRAM_V113`.

Verified three ways, not one:

- the repo's own `make check` (`sha1sum -c mzm_us.sha1`) → `mzm_us.gba: OK`;
- an independent `cmp` of the built file against `~/decompfleet/roms/mzm.gba`;
- **a genuine clean rebuild.** `make tidy DATA=1` really does empty the tree — it
  deletes the ELF, the ROM, every `.o`, every generated `src/**/*.s`, `data/`,
  `include/extracted/` and `sound/direct_sound_samples/` (I checked each was gone,
  rather than assuming, after another session on this fleet found a stale `.elf`
  letting `make` "reproduce" a ROM in 2.9 s). Re-extracting and rebuilding from
  that empty tree took 11 s wall / 69 s CPU at `-j12` and produced a file
  identical to both the staged ROM and the first build.

### Pinned revision

```
metroidret/mzm  43b7fd52f552e4d38c1521ff9d4df5ee57e61493   (2026-08-23)
                "Fix a couple typos"
```

Checked out detached at that commit; not a moving branch. (The repo's default
branch is `main`, though its README and CI workflow both still say `master`.)

### Only the US build

`make us` / `REGION=us` only. The repo also supports `eu`, `jp`, `us_beta` and
`eu_beta`, and a `DEBUG=1` variant of each — none of those were built, because
there is no baserom for them staged here and the debug build additionally needs
data extracted from the US *beta* ROM. `cn` is listed as not yet supported upstream.

### The real completion figures, at this revision

From the repository itself, not quoted from anywhere else:

- `README.md`: **2718/2721 functions decompiled (99.89%)**, 3 left.
- `README.md`: **0x76b014 / 0x76b014 bytes of data not in blobs (100%)**, 0 left.
- `docs/non_matching_functions.md` names the three: `BitFill` (`src/dma.c`,
  register swaps), `FileSelectCopyFileHandler` and `FileSelectUpdateTilemap`
  (`src/menus/file_select.c`, incorrect branches / register swaps).

What I verified myself rather than taking on trust:

- There are **exactly three** `#ifdef NON_MATCHING` function sites in `src/`, and
  they are the three named in the docs. `NON_MATCHING` is **off** in this build:
  `include/config.h` has it commented out, and the `#define` further down that
  file is inside `#ifdef MODERN`. So the matching ROM uses the hand-written `asm`
  form of all three, and the C rewrites are compiled out.
- Counting what is actually in the US non-debug build: **2618 functions compiled
  from C** (`.type <name>,function` directives in the generated `src/**/*.s`,
  which agbcc emits for `static` functions too) plus **39 hand-written assembly
  functions** in `asm/` — `crt0.s` (1), `intr_main.s` (1), `romheader.s` (1),
  `syscalls.s` (12 BIOS SWI wrappers), and the sound driver, which is still
  assembly: `audio_internal.s` (14) and `soundcode.s` (10). Total **2657**.
- The gap to 2721 is code this build does not compile: I count **53 function
  definitions in `src/` sitting behind `#ifdef DEBUG`**, plus region-gated code.
  I did **not** reconcile the last ~11 function-for-function, so treat 2718/2721
  as the project's own census across its configurations and 2657 as the count for
  this particular ROM. Both numbers are in this file so neither is doing work the
  other should.

The census figures I was given (2718/2721, 99.89%, data 100%) match the repo
exactly at this revision.

---

## 2. Toolchain — and the thing that is different about this repo

**mzm does not use pret/agbcc, and the fleet's shared agbcc cannot build it.**

`Makefile` sets `CFLAGS = -Werror -O2 -mthumb-interwork -fhex-asm -f2003-patch`.
`~/decompfleet/shared/agbcc-install` (pret/agbcc, built by the pokefirered agent)
rejects `-f2003-patch` outright:

```
$ ~/decompfleet/shared/agbcc-install/bin/agbcc ... -f2003-patch
agbcc: Invalid option `-f2003-patch'
```

The flag only exists in **jiangzhengwenjz/agbcc** (`gcc/toplev.c:736`), which is
what the README and the CI workflow both name. I built that fork rather than
touching the shared one, and installed it to a **new** shared prefix so the next
non-pret GBA agent does not have to repeat it:

```
~/decompfleet/shared/agbcc-jzw-install/      13 MB, bin/ include/ lib/
```

| component | version / revision | notes |
|---|---|---|
| agbcc | jiangzhengwenjz/agbcc `59b966ed1b8f371856dcf99f1546c2fe89c678ca` (2026-08-07) | built from source, `bash build.sh` |
| | reports `GNU C version 2.9-arm-000512 (thumb-elf)` | |
| arm-none-eabi binutils | 2.45.50.20251209 | shared `~/decompfleet/shared/arm-bin` |
| host gcc / g++ | 15.2.0 (Ubuntu 15.2.0-16ubuntu1) | also the C preprocessor — see below |
| python3 | 3.14.4 | the extractor and `tools/preproc.py` |
| emscripten | 4.0.10 (`b7dc6e57`) | shared emsdk |
| node | 24.19.0 | from the shared emsdk, for the core test |
| mGBA | `68cf79fe55257b61efaeea87c83de69aab01f963` | `libmgba.a` built by the pokeruby agent; I copied the headers and the archive rather than rebuilding it |
| Chrome | 151.0.7922.108 | headless, software GL |

sha1 of the agbcc binaries, so a future session can tell whether they were replaced:

```
478f8e96e38a7fe9dd24616b7b438c06dc2e687f  bin/agbcc
91eced9c45ae05a42722d485b99d8da14d5d1c0d  bin/agbcc_arm
198944e3d5340e5a64e8066853d1d2795b994a55  bin/old_agbcc
adb1855f31d0a487e55fbeaea9ba4354d30588cf  lib/libc.a
c61ad82531caa6e661ba0aadafc898b86e128793  lib/libgcc.a
```

It built clean on the first try with host gcc 15.2.0 — no patches, no CFLAGS
overrides, about a minute. (Run `build.sh` under `bash`, not `sh`: it uses `&>`,
which dash parses as a background `&` followed by a redirect.)

### The recipe, end to end

```sh
export PATH="$HOME/decompfleet/shared/arm-bin/usr/bin:$PATH"

# agbcc (already built and shared; this is how it got there)
git clone https://github.com/jiangzhengwenjz/agbcc.git && cd agbcc && bash build.sh
./install.sh <path to the mzm checkout>       # writes <tree>/tools/agbcc/{bin,include,lib}

cd <tree>
cp ~/decompfleet/roms/mzm.gba mzm_us_baserom.gba
python3 tools/extractor.py                    # pulls the game's data OUT OF THE USER'S ROM
make -j"$(nproc)"
make check                                    # sha1sum -c against the repo's own .sha1
```

Three things the README implies that turned out not to matter on this box:

- **No sudo, no devkitARM.** `TOOLCHAIN` here is a *command prefix*
  (`arm-none-eabi-`), not a directory, and only `as`, `ld`, `objcopy` and
  `objdump` are used. The Ubuntu binutils deb already unpacked at
  `~/decompfleet/shared/arm-bin` covers all four.
- **No `arm-none-eabi-cpp`.** Unlike the pret Makefiles, mzm sets `CPP = cpp` —
  the *host* preprocessor, with `-nostdinc -Iinclude/`. A binutils-only prefix
  would have been enough for this repo.
- **No host-tool patching.** The only host tool is `tools/gbafix`, which compiles
  with gcc 15 unchanged.

`tools/extractor.py` reads 18 MB of `data/`, 33 MB of `include/extracted/` and
2.1 MB of sound samples straight out of `mzm_us_baserom.gba` at build time. That
is the fleet's bring-your-own-ROM model working by construction: none of it is in
this repository and `.gitignore` excludes all of it.

---

## 3. The browser target

Not EmulatorJS and not a libretro frontend. A direct emscripten build of mGBA's
`mCore` API plus a ~400-line C shim (`web/src/shim.c`) and one static HTML page —
the stack the pokeruby agent established, reused here because it is small,
dependency-free and sidesteps every EmulatorJS trap in the shared notes by not
having EmulatorJS in it.

```
web/dist/mzm.wasm    663 403 bytes
web/dist/mzm.js       19 253
web/dist/index.html   23 387
```

### What is MZM-specific about it

- **The save media is 32 KiB SRAM**, not flash. Zero Mission's own signature in
  the ROM is `SRAM_V113`. The shim's buffer is exactly 32768 bytes — oversizing
  it would make mGBA pick the wrong media — and `vj_save_type()` /
  `vj_save_media_size()` report what mGBA *actually* attached so the test can
  assert `GBA_SAVEDATA_SRAM` / 32768 rather than assume it.
- **`vj_read8/16/32` expose the bus**, so the test harness can gate "played" on
  the game's own IWRAM state rather than on a picture.
- The page refuses a ROM whose header title is not `ZEROMISSION*`, with the
  header it actually saw in the message. A different GBA game would get the wrong
  save media from this page, so this is a correctness check, not branding.

### The ROM stays here

- A real `<input type=file>` plus drag-and-drop; read with the File API into wasm
  linear memory. The headless test feeds it with CDP `DOM.setFileInputFiles` on
  that same element, so the path under test is the one a person uses.
- **The Content-Security-Policy is the boundary**, not the JS wrapper:
  `default-src 'none'; connect-src 'self' blob: data:; form-action 'none'` and
  friends, in a `<meta http-equiv>` on the page. The browser enforces it, no
  script on the page can reach another origin even if it tries, and there is no
  startup race to win. The `fetch`/`XHR`/`WebSocket`/`sendBeacon` wrapper is kept
  only so the page can *log* an attempt — it is belt-and-braces, and the test
  asserts at the CDP network layer either way.
- The ROM is cached in IndexedDB so a reload does not re-prompt, with a **Forget
  stored ROM** button. It never leaves the device. The page is static files with
  no server component.
- **Muted by default, and genuinely silent**: no `AudioContext` is constructed at
  all until the user clicks the sound button. The test asserts the count of
  constructed `AudioContext` objects is zero, so "muted" means there is no audio
  graph, not a gain node at zero.

### How "played" is decided

From `web/gen/symbols.json`, generated by `tools/gen_symbols.py` out of the ELF
that this matching ROM was objcopy'd from — never from a wiki or a found memory
map. Two sources, cross-checked against each other:

- symbol addresses and sizes from `arm-none-eabi-nm -S` on `mzm_us.elf`;
- struct field offsets and enum values from a host-gcc probe that `#include`s the
  decomp's own headers and prints `offsetof`/`sizeof`.

The run counts as gameplay only when the game says so:

```
gMainGameMode == GM_INGAME (4)
gSubGameMode1 == SUB_GAME_MODE_PLAYING (2)
gDemoState    == DEMO_STATE_NONE (0)
```

and Samus's own `gSamusData.xPosition` / `yPosition` change while that holds.
Zero Mission has a title-screen attract demo that is screenshot-identical to
playing; `gDemoState` is what separates them, and a picture cannot.

Two traps this hit, both worth knowing:

- **"Reached gameplay" is not "has control."** Zero Mission hands you a short
  `GM_INGAME` window while the ship lands and takes it away again for the arrival
  cutscene. A first-sighting test starts moving inside the cutscene and then
  correctly reports that nothing moved. The gate requires **600 unbroken frames**
  of the playing state with no input before the movement phase begins.
- **The host compiler's `sizeof` is the wrong stride.** agbcc rounds every struct
  up to a 4-byte multiple: `struct SamusData` is 30 bytes of fields but 32 on the
  target, and `struct SaveFileInfo` 22 and **24**. Indexing `gSaveFilesInfo[1]`
  at +22 would have read six bytes short of the field, silently, forever. The
  generator takes the stride from the ELF's own `.size` and refuses to emit
  anything if the two sources disagree by 4 bytes or more. It caught this.

---

## 4. What I actually tested

### Matching build — verified
Byte-identical, by `sha1sum -c`, by `cmp`, and reproduced from a genuinely
emptied tree. Hashes in §1.

### Core, in node (`web/test/core-smoke.mjs`), muted — verified
`node test/core-smoke.mjs ~/decompfleet/roms/mzm.gba gen/symbols.json`, all checks pass:

- header reads back `ZEROMISSIONEBMXE`, revision 0, 8388608 bytes;
- reached gameplay by frame 1749 and held it 600 unbroken frames; the modes seen
  on the way in were soft-reset → intro → title → file select → in-game →
  cutscene → in-game, and the attract demo never ran;
- **900 frames of movement, 900 of them inside the playing state**: 738 distinct
  `xPosition` values, 115 distinct `yPosition`, 8 distinct Samus poses, in-game
  timer +900 ticks;
- non-black frame (mean luma 37.0) — and **I looked at the frames**, not just the
  number. The PPM dumps show Samus standing on the Brinstar start platform, mid-jump
  over a Zeela, and in the next room along;
- mGBA attached save type 1 (`GBA_SAVEDATA_SRAM`), media size 32768;
- savestate round trip reproduces the **exact** frame hash after 240 diverging
  frames, and the test fails itself as inconclusive if the picture did not change
  during the divergence window;
- the save round trip, judged by the game (see below).

### "The save persisted" — judged by the game, with three arms

A byte diff across a reload cannot tell a complete save from a half-written one.
So the question is put to Zero Mission's own validation code:
`SramRead_All → SramTestFlash` sets `gSramCorruptFlag`, and
`unk_743a4 → unk_74574 → unk_74624` runs the game's sanity checks over each file
and writes the verdict into `gSaveFilesInfo[i].corruptionFlag`.
`SramWrite_FileInfo` then copies `gameCompletion.introPlayed` out of SRAM **only**
for a file those checks accepted.

| arm | what was handed back | the game's verdict at the file select screen |
|---|---|---|
| restored | the SRAM we stored | `gSramCorruptFlag=0`, `corruptionFlag=0`, **`introPlayed=1`** |
| blank | 32 KiB of zeros | `introPlayed=0` |
| scrambled | the same save with bytes 0x40–0x4000 XOR 0xFF | **`introPlayed=0`** — rejected |

The scrambled arm is the one that makes this mean something: it shows the check
is live and that a damaged save does *not* come back as a good one. The capture
is also taken only after the SRAM hash has stopped moving for 120 frames, because
the post-intro save (`SramProcessIntroSave`) is a multi-stage write and catching
it mid-flight is exactly how a byte-identical-but-rejected save happens.

### The real page, headless Chrome (`web/test/browser_check.py`), muted — **33/33**

Three stages in one run, each writing its stage number into its own report and
asserting it on read, plus a per-navigation nonce the page must echo back.
Ephemeral debug port and a private profile — a fixed port on this box attaches
you to another agent's browser.

Stage 1 — boot, play, persist:
- the tab answering is the one we navigated (nonce echo), stage marker is 1;
- the canvas has a non-zero laid-out size (960×640 css px) — asserted explicitly,
  because a core renders perfectly into a 0×0 canvas and still reports success;
- ROM accepted through the real file input; header `ZEROMISSIONE` / `BMXE` / rev 0
  / 8388608;
- reached and held 600 unbroken frames of gameplay;
- **29–30 distinct x positions over 30 samples, every sample inside the playing
  state with the demo flag clear**; in-game timer advanced;
- mGBA attached 32 KiB SRAM;
- not black, measured three ways: wasm-side luma 33.3, page canvas 33.3, and the
  **screenshot the browser handed back**, cropped to the canvas rect, mean luma
  32.2 with 31% of pixels lit. The driver's screenshot is the one that counts;
- **no `AudioContext` was ever constructed**;
- at the CDP network layer the page made 4 requests and all were its own origin:
  `index.html`, `mzm.js`, `mzm.wasm`, `favicon.ico`. The page's own wrapper logged
  nothing blocked either;
- the SRAM settled before capture; 32 KiB landed in IndexedDB;
- **dispatching `beforeunload`, `pagehide` and `visibilitychange` did not empty
  the store.** Dispatched, not navigated: a navigate-away soak is a weak detector
  that passed 8/8 on a page that was provably destroying its save.

Stage 2 — a real same-tab navigation:
- on the fresh load, **before any ROM is picked**, the 32 KiB blob is still there
  byte for byte (so nothing is running that could rewrite what is being measured);
- the page fed exactly those bytes into the core **before** `loadROM` — mGBA reads
  that buffer when it attaches the save media, so restoring afterwards would be
  silently ignored. Asserted on the page's own record of what it restored, not on
  a read-back, which would race the game's boot-time SRAM writes;
- drove to the game's file select screen: `gSramCorruptFlag=0`,
  `gSaveFilesInfo[0].corruptionFlag=0`, `introPlayed=1`. The screenshot shows file
  A reading **NORMAL / 99 / CRATERIA** where the other two slots are blank.

Stage 3 — the negative control, separate browser, clean profile, `?nosaveflush=1`:
- the arm proves it is a different page (it logs that its flushing is disabled)
  and reaches gameplay too, so the save path is the only difference;
- it persisted **nothing**;
- and with nothing restored the same file select screen reports `introPlayed=0`,
  and the screenshot shows file A blank. So stage 2's pass is the save path
  working, not the test being unable to tell the difference.

`tools/save_mechanism.py` runs the mechanism question on its own, both arms, in
about 2 minutes, and prints the before/after table.

### What I did NOT test — be clear about this

- **No long-form play.** The run reaches the first rooms of Brinstar and no
  further. Nothing beyond that is play-tested: no boss, no item, no elevator, no
  ending. I did not chase any of them.
- **I never reached an in-game save station.** The save this test round-trips is
  the one the game writes itself after the intro (`SramProcessIntroSave`), which
  is why `exists` and `hasSaved` are 0 on the file select screen and the
  `area`/`energy` shown there (Crateria / 99) are the defaults the game derives
  from `introPlayed` rather than stored values. "Press SAVE at a save station,
  reload, and continue from it" is **unverified**.
- The **scrambled-save arm runs in the node core test only.** The browser test has
  the blank control but not the scrambled one; I did not add a test-only hook for
  writing corrupt bytes into the page's IndexedDB.
- **Only headless Chrome 151**, on software GL. No Firefox, no Safari, no real
  mobile browser, no real GPU.
- **No touch controls.** Keyboard and gamepad only, and the gamepad path is
  written against the Standard Gamepad mapping but **unverified** — no controller
  was attached.
- **Audio is unverified.** The code to drain mGBA's buffer into a ScriptProcessor
  exists and is wired to the sound button, but every test ran muted by rule, so I
  have never heard it. Treat sound as untested.
- **Only the US ROM.** EU, JP, the two beta ROMs and every `DEBUG=1` variant were
  not built and not run.
- `make modern` (the `MODERN=1` non-matching build) was not attempted.
- Link cable / multiboot features are not available in the browser and were not
  attempted.
- The **three remaining non-matching functions were not worked on.** This session
  built the decomp; it did not advance it.

### A note on the forks the census lists

I used **`metroidret/mzm`**, the canonical repo, and it is the right choice here:
it already matches byte-for-byte. The three unmatched functions are *decompilation*
debt (they are hand-written asm in the matching build), not *matching* debt — a
fork that closes them would produce the same 8 MB of bytes. So nothing about this
deliverable would change by using one.

I did not build `BloodLantern/mzm_pc` or `MetroidZeroMissionRecomp`, and I did not
inspect them, so I am making no claim about what either one does. What I can say is
the shape of the problem for *any* PC or recomp target built from this tree:
`tools/extractor.py` pulls **3 926 database entries — 7 770 files, 53 MB** of
graphics, tilemaps, room data and sound samples — out of the user's cartridge dump
and the build links them in. A native binary produced the normal way therefore has
the game's art and music inside it, which by the fleet's rule is not shippable, and
the alternative is a non-matching build. That is the same judgement the Twilight
Princess and Castlevania sessions reached after getting native targets to build.
Running the matching ROM on a wasm core keeps the user's own dump as the only source
of those bytes, which is why that is what shipped here.

---

## 5. Proposed catalog fragment

Do not merge mechanically — `source`, `embedUrl`, `image` and `verification.date`
are placeholders until this is actually published.

```json
{
  "id": "metroid-zero-mission",
  "title": "Metroid: Zero Mission",
  "aliases": ["mzm", "zero mission", "metroid zero mission", "metroid 1 remake"],
  "year": 2004,
  "genre": "Action-adventure",
  "kind": "Decompilation",
  "mode": "files",
  "engine": "the metroidret/mzm decompilation, built to a matching ROM and run on a 663 KB WebAssembly build of mGBA's core",
  "description": "Samus returns to Zebes: explore, find upgrades, and open the way down.",
  "overview": "Metroid: Zero Mission built from the metroidret/mzm decompilation, pinned at 43b7fd52. The build is byte-identical to a retail (USA, Australia) cartridge dump, sha1 5de8536afe1f0078ee6fe1089f890e8c7aa0a6e8. The page runs it on a direct WebAssembly build of mGBA's core, with no emulator frontend in between. You supply your own ROM: it is read in your browser with the File API straight into the emulator's memory. The page is static files with no server component, and a Content-Security-Policy stops any script on it reaching another origin.",
  "source": "https://github.com/andrewnakas/mzm-web",
  "engineLicense": "metroidret/mzm; mGBA core MPL-2.0",
  "assetLicense": "None distributed — the player supplies their own ROM",
  "assetRequirements": "Your own Metroid: Zero Mission ROM, (USA, Australia), sha1 5de8536afe1f0078ee6fe1089f890e8c7aa0a6e8. It is read in your browser and never uploaded.",
  "controls": [
    "Arrow keys — move",
    "X — A (jump)",
    "Z — B (shoot)",
    "A / S — L / R",
    "Enter — Start",
    "Shift — Select",
    "P — pause",
    "Gamepad — any standard controller (untested)"
  ],
  "saveInstructions": "Save at a save station in the game. The cartridge's 32 KiB SRAM is mirrored into this browser's storage and handed back to the emulator automatically on reload. Clearing site data removes it, along with the cached ROM.",
  "limitations": [
    "You must supply your own ROM; none is distributed.",
    "The USA ROM only — the European, Japanese and beta dumps are different builds and are untested here.",
    "Verified only as far as the first rooms of Brinstar. No boss, no item, no ending has been play-tested.",
    "Save persistence is verified by round-tripping the cartridge SRAM through browser storage and having the game's own checksum code accept it; reaching an in-game save station and continuing from it was not tested.",
    "Sound is implemented but has never been listened to — all testing was muted.",
    "Keyboard and gamepad only; no on-screen touch controls, and the gamepad path is untested.",
    "Tested in headless Chrome only.",
    "Link cable and multiboot features are not available in the browser."
  ],
  "inputs": ["Keyboard", "Gamepad"],
  "downloadMB": 1,
  "complete": false,
  "color": "#d94f3d",
  "shelf": "decomp",
  "platform": "Game Boy Advance",
  "launch": "embed",
  "touch": false,
  "embedUrl": "https://andrewnakas.github.io/mzm-web/",
  "verification": {
    "status": "smoke-tested",
    "date": null,
    "browsers": ["Chrome 151 (headless)"],
    "scope": "Muted headless run: boot through the intro and file select into Brinstar, with the game's own gMainGameMode/gSubGameMode1/gDemoState confirming real gameplay rather than the attract demo, and Samus's own coordinates confirming movement. Cartridge SRAM round-tripped through browser storage across a real page navigation and accepted by the game's own validation, with blank and scrambled control arms. No play-testing beyond the first rooms.",
    "playthrough": false
  },
  "image": "/images/metroid-zero-mission.png"
}
```

---

## 6. Layout

```
games/mzm/
  STATUS.md                  this file
  .gitignore                 no ROMs, no extracted assets, no build output
  upstream/mzm/              metroidret/mzm @ 43b7fd52 (detached), not vendored
  toolchain/agbcc-src/       jiangzhengwenjz/agbcc @ 59b966ed, built here
  ref/mgba/                  mGBA @ 68cf79fe: headers + libmgba.a, copied from
                             the pokeruby agent's build rather than rebuilt
  tools/
    gen_symbols.py           game-state addresses, derived from OUR ELF + headers
    cdp.py                   CDP client, ephemeral debug port (no websocket lib here)
    pngstat.py               stdlib PNG brightness, for the driver's screenshots
    serve.py                 static server for web/dist, ephemeral port
    save_mechanism.py        the unload-destroys-the-save question, both arms
    save_mechanism_pokefirered_reference.py   the EmulatorJS original, for reference
  web/
    Makefile                 all / test / selftest / browsercheck / savecheck / check
    src/shim.c               mCore -> JS surface
    src/index.html           the page
    test/core-smoke.mjs      core test in node (VJ_DUMP=dir for PPM frames)
    test/browser_check.py    three-stage CDP harness, drives the real file picker
    gen/symbols.json         generated; gitignored
    dist/                    build output; gitignored
```

Reproduce everything:

```sh
export PATH="$HOME/decompfleet/shared/arm-bin/usr/bin:$PATH"
cd upstream/mzm && cp ~/decompfleet/roms/mzm.gba mzm_us_baserom.gba \
  && python3 tools/extractor.py && make -j"$(nproc)" && make check
cd ../../web && make && make symbols && make selftest && make browsercheck
```
