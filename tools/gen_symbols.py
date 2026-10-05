#!/usr/bin/env python3
"""Derive the game-state addresses the browser tests gate on, from OUR build.

Nothing here is hardcoded from a wiki or a memory map found online. Two sources,
both inside the repository we just built byte-identically to the retail ROM:

  1. symbol addresses  -- `arm-none-eabi-nm` on mzm_us.elf, the ELF the matching
     mzm_us.gba was objcopy'd out of;
  2. struct offsets and enum values -- a host-gcc probe that #includes the
     decomp's own headers and prints offsetof()/sizeof()/enumerator values.

(1) and (2) are cross-checked against each other: the distance from a struct's
symbol to the next symbol in address order must be >= the sizeof() the probe
reports, and the probe's sizeof() must fit. A mismatch is a hard error, because
an offset that is quietly wrong produces a test that confidently reads the wrong
byte and grades itself on it.

    python3 tools/gen_symbols.py --out web/gen/symbols.json
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
PROJECT = HERE.parent
UPSTREAM = PROJECT / "upstream" / "mzm"
ARM_BIN = pathlib.Path.home() / "decompfleet" / "shared" / "arm-bin" / "usr" / "bin"

# Plain scalars we only need an address for.
SCALARS = {
    "gMainGameMode": 2,
    "gSubGameMode1": 2,
    "gSubGameMode2": 1,
    "gSubGameMode3": 1,
    "gSubGameModeStage": 1,
    "gDemoState": 1,
    "gFrameCounter16Bit": 2,
    "gFrameCounter8Bit": 1,
    "gCurrentArea": 1,
    "gDifficulty": 1,
    "gHasSaved": 1,
    "gMostRecentSaveFile": 1,
    "gIsLoadingFile": 1,
    "gButtonInput": 2,
    "gPauseScreenFlag": 1,
    "gPreviousXPosition": 2,
    "gPreviousYPosition": 2,
    # The game's own verdict on whether the SRAM it just read is usable.
    # SramRead_All -> SramTestFlash sets this; a byte-identical-but-half-written
    # save is exactly what it exists to catch.
    "gSramCorruptFlag": 1,
    "gSramOperationStage": 1,
}

# Structs: address from nm, field offsets from the probe.
STRUCTS = {
    "gSamusData": ("SamusData", ["pose", "xPosition", "yPosition",
                                 "xVelocity", "yVelocity", "direction"]),
    "gEquipment": ("Equipment", ["maxEnergy", "currentEnergy", "maxMissiles",
                                 "currentMissiles", "suitType"]),
    "gInGameTimer": ("InGameTimer", ["hours", "minutes", "seconds", "frames"]),
    # An array of 3. Address + element size are enough for the harness to index
    # it; this is the file-select screen's own view of what is in SRAM, which is
    # how the save test asks the game whether it read our restored save back.
    "gGameCompletion": ("GameCompletion", ["completedGame", "introPlayed",
                                           "language"]),
    "gSaveFilesInfo": ("SaveFileInfo", ["exists", "corruptionFlag", "currentArea",
                                        "currentEnergy", "maxEnergy", "igtHours",
                                        "igtMinutes", "igtSeconds", "hasSaved",
                                        "introPlayed", "difficulty"]),
}

ENUMS = [
    # (C name, header it comes from)
    "GM_SOFT_RESET", "GM_INTRO", "GM_TITLE", "GM_FILE_SELECT", "GM_INGAME",
    "GM_MAP_SCREEN", "GM_GAMEOVER", "GM_CHOZODIA_ESCAPE", "GM_CREDITS",
    "GM_TOURIAN_ESCAPE", "GM_CUTSCENE", "GM_DEMO", "GM_GALLERY",
    "GM_FUSION_GALLERY", "GM_START_SOFT_RESET", "GM_ERASE_SRAM",
    "SUB_GAME_MODE_DOOR_TRANSITION", "SUB_GAME_MODE_PLAYING",
    "SUB_GAME_MODE_LOADING_ROOM", "SUB_GAME_MODE_DYING",
    "DEMO_STATE_NONE", "DEMO_STATE_RECORDING_DEBUG", "DEMO_STATE_PLAYING",
    "DEMO_STATE_STARTING",
    "SUIT_NORMAL", "SUIT_FULLY_POWERED", "SUIT_SUITLESS",
]

# Arrays, so the nm-gap cross-check knows how many elements to expect.
ARRAY_COUNTS = {"gSaveFilesInfo": 3}

PROBE_HEADERS = [
    "types.h",
    "structs/samus.h",
    "structs/game_state.h",
    "structs/in_game_timer.h",
    "structs/save_file.h",
    "constants/demo.h",
    "structs/demo.h",
]


def nm_symbols(elf: pathlib.Path) -> dict:
    """Address AND size for every defined symbol, from the ELF's own .size.

    `nm -S` is the authority on how big a thing really is on ARM: agbcc rounds
    every struct up to a 4-byte multiple (struct SamusData is 30 bytes of fields
    but 32 bytes on the target, struct SaveFileInfo 22 and 24), so a host
    sizeof() is the wrong stride for an array and reading element 1 at +22 would
    land six bytes short with nothing to show for it.
    """
    nm = shutil.which("arm-none-eabi-nm") or str(ARM_BIN / "arm-none-eabi-nm")
    out = subprocess.run([nm, "-S", "-n", "--defined-only", str(elf)],
                         check=True, capture_output=True, text=True).stdout
    syms: dict[str, dict] = {}
    for line in out.splitlines():
        m = re.match(r"^([0-9a-fA-F]{8})\s+(?:([0-9a-fA-F]+)\s+)?\S\s+(\S+)$",
                     line.strip())
        if not m:
            continue
        name = m.group(3)
        if name in syms:
            continue
        syms[name] = {"addr": int(m.group(1), 16),
                      "size": int(m.group(2), 16) if m.group(2) else None}
    return syms


def build_probe(src_root: pathlib.Path) -> dict:
    """Compile and run a host probe over the decomp's own headers."""
    lines = ['#include <stdio.h>', '#include <stddef.h>']
    for h in PROBE_HEADERS:
        lines.append(f'#include "{h}"')
    lines.append("int main(void) {")
    for cname, (sname, fields) in STRUCTS.items():
        lines.append(f'  printf("S {sname} %zu\\n", sizeof(struct {sname}));')
        for f in fields:
            lines.append(f'  printf("O {sname} {f} %zu\\n",'
                         f' offsetof(struct {sname}, {f}));')
    for e in ENUMS:
        lines.append(f'  printf("E {e} %d\\n", (int) {e});')
    lines.append("  return 0; }")
    with tempfile.TemporaryDirectory() as td:
        tdp = pathlib.Path(td)
        (tdp / "probe.c").write_text("\n".join(lines) + "\n")
        exe = tdp / "probe.bin"
        # include/macros.h has no include guard, so STATIC_ASSERT gets its real
        # definition back every time any header pulls macros.h in again -- which
        # means neutering it from the probe does not stick. Shadow the handful of
        # headers that actually use it instead, with those lines stripped. The
        # assertions concern the ARM layout (e.g. struct Sram fitting in 32 KiB)
        # and do not hold on the host; the ELF-size cross-check below is what
        # keeps the host layout honest.
        inc = src_root / "include"
        shadow = tdp / "shadow"
        for h in inc.rglob("*.h"):
            text = h.read_text()
            if "STATIC_ASSERT(" not in text:
                continue
            rel = h.relative_to(inc)
            dst = shadow / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(re.sub(r"^\s*STATIC_ASSERT\(.*$", "", text, flags=re.M))
        subprocess.run(["gcc", "-DREGION_US", "-I", str(shadow), "-I", str(inc),
                        "-o", str(exe), str(tdp / "probe.c")], check=True)
        out = subprocess.run([str(exe)], check=True,
                             capture_output=True, text=True).stdout
    sizes: dict[str, int] = {}
    offs: dict[str, dict[str, int]] = {}
    enums: dict[str, int] = {}
    for line in out.splitlines():
        p = line.split()
        if p[0] == "S":
            sizes[p[1]] = int(p[2])
        elif p[0] == "O":
            offs.setdefault(p[1], {})[p[2]] = int(p[3])
        elif p[0] == "E":
            enums[p[1]] = int(p[2])
    return {"sizes": sizes, "offsets": offs, "enums": enums}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--elf", type=pathlib.Path,
                    default=UPSTREAM / "mzm_us.elf")
    ap.add_argument("--src", type=pathlib.Path, default=UPSTREAM)
    ap.add_argument("--out", type=pathlib.Path, required=True)
    a = ap.parse_args()

    if not a.elf.exists():
        print(f"no {a.elf} -- build the matching ROM first (make -C "
              f"{a.src} REGION=us)", file=sys.stderr)
        return 1

    syms = nm_symbols(a.elf)
    probe = build_probe(a.src)

    out: dict = {
        "source": {
            "elf": str(a.elf.name),
            "repo": "metroidret/mzm",
            "revision": subprocess.run(
                ["git", "-C", str(a.src), "rev-parse", "HEAD"],
                capture_output=True, text=True).stdout.strip(),
        },
        "scalars": {},
        "structs": {},
        "enums": probe["enums"],
    }

    missing = []
    for name, width in SCALARS.items():
        if name not in syms:
            missing.append(name)
            continue
        sz = syms[name]["size"]
        if sz is not None and sz < width:
            print(f"{name} is {sz} bytes in the ELF but this tool wants to read "
                  f"{width}", file=sys.stderr)
            return 2
        out["scalars"][name] = {"addr": syms[name]["addr"], "width": width,
                                "elfSize": sz}

    for cname, (sname, fields) in STRUCTS.items():
        if cname not in syms:
            missing.append(cname)
            continue
        addr = syms[cname]["addr"]
        elfSize = syms[cname]["size"]
        hostSize = probe["sizes"][sname]
        count = ARRAY_COUNTS.get(cname, 1)
        if elfSize is None:
            print(f"{cname} has no .size in the ELF; refusing to guess a stride",
                  file=sys.stderr)
            return 2
        if elfSize % count:
            print(f"{cname}: ELF size {elfSize} is not a multiple of its "
                  f"declared element count {count}", file=sys.stderr)
            return 2
        stride = elfSize // count
        # The host compiler supplies the field offsets; the ELF supplies the
        # real element stride. They must agree to within agbcc's 4-byte struct
        # rounding, or the two sources describe different layouts and every
        # offset below is suspect.
        if not (hostSize <= stride and stride - hostSize < 4):
            print(f"layout cross-check FAILED for {cname}: the ELF says each "
                  f"element is {stride} bytes, the decomp's headers compiled "
                  f"for the host say {hostSize}. Refusing to emit offsets.",
                  file=sys.stderr)
            return 2
        out["structs"][cname] = {
            "addr": addr,
            "type": sname,
            "stride": stride,
            "count": count,
            "elfSize": elfSize,
            "hostSize": hostSize,
            "fields": probe["offsets"][sname],
        }

    if missing:
        print(f"symbols not in {a.elf}: {', '.join(missing)}", file=sys.stderr)
        return 3

    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
    print(f"{a.out}: {len(out['scalars'])} scalars, {len(out['structs'])} "
          f"structs, {len(out['enums'])} enum values "
          f"(rev {out['source']['revision'][:8]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
