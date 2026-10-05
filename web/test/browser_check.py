#!/usr/bin/env python3
"""The real page, in headless Chrome, driven through its own file picker.

Three stages, in one run, with the stage number written into every report and
asserted on read -- a report that does not say which stage produced it is not
evidence, and Chrome's crash-restore has been caught handing one stage the
previous stage's page on this box.

  stage 1  boot, play, and prove the play is play: the game's own IWRAM state
           (addresses from web/gen/symbols.json, generated from the matching
           build's ELF) must say GM_INGAME / SUB_GAME_MODE_PLAYING with
           gDemoState clear while Samus's coordinates change. Then flush the
           cartridge SRAM to IndexedDB and prove no unload handler destroys it,
           by dispatching the events rather than navigating -- a navigate-away
           soak is a weak detector that passes on a provably broken page.
  stage 2  a REAL same-tab navigation, then read IndexedDB before any ROM is
           picked, re-pick the ROM, and drive only as far as the game's file
           select screen. The game must show the restored file's own data.
  stage 3  the negative control, in a separate browser with a clean profile and
           ?nosaveflush=1: the same journey must FAIL to persist. Without this
           arm, stage 2 passing means nothing.

    python3 web/test/browser_check.py <rom> [--shots DIR] [--head]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
PROJECT = HERE.parent.parent
sys.path.insert(0, str(PROJECT / "tools"))
import cdp        # noqa: E402
import pngstat    # noqa: E402

SYMS = json.loads((PROJECT / "web" / "gen" / "symbols.json").read_text())
S, ST, E = SYMS["scalars"], SYMS["structs"], SYMS["enums"]

results: list[tuple[bool, str]] = []


def check(ok: bool, msg: str):
    results.append((bool(ok), msg))
    print(("  PASS  " if ok else "  FAIL  ") + msg, flush=True)
    return ok


def info(msg: str):
    print("        " + msg, flush=True)


# ---------------------------------------------------------------- page state

def read_state(tab) -> dict:
    """One round trip for the whole of the game state we care about."""
    f = ST["gSamusData"]["fields"]
    t = ST["gInGameTimer"]["fields"]
    expr = """(function(){var m=window.__mzm;if(!m||!m.started)return null;return {
      gm:   m.read16(%d),
      sub:  m.read16(%d),
      demo: m.read8(%d),
      x:    m.read16(%d),
      y:    m.read16(%d),
      pose: m.read8(%d),
      energy: m.read16(%d),
      igt:  [m.read8(%d),m.read8(%d),m.read8(%d),m.read8(%d)],
      frames: m.frames()
    };})()""" % (
        S["gMainGameMode"]["addr"], S["gSubGameMode1"]["addr"], S["gDemoState"]["addr"],
        ST["gSamusData"]["addr"] + f["xPosition"],
        ST["gSamusData"]["addr"] + f["yPosition"],
        ST["gSamusData"]["addr"] + f["pose"],
        ST["gEquipment"]["addr"] + ST["gEquipment"]["fields"]["currentEnergy"],
        ST["gInGameTimer"]["addr"] + t["hours"], ST["gInGameTimer"]["addr"] + t["minutes"],
        ST["gInGameTimer"]["addr"] + t["seconds"], ST["gInGameTimer"]["addr"] + t["frames"])
    return tab.js(expr)


def read_files(tab) -> list:
    """The game's own file-select view of what is in SRAM."""
    b, st, f = ST["gSaveFilesInfo"]["addr"], ST["gSaveFilesInfo"]["stride"], \
        ST["gSaveFilesInfo"]["fields"]
    parts = []
    for i in range(ST["gSaveFilesInfo"]["count"]):
        o = b + i * st
        parts.append("{exists:m.read8(%d),corruptionFlag:m.read8(%d),"
                     "introPlayed:m.read8(%d),area:m.read8(%d),"
                     "energy:m.read16(%d),hasSaved:m.read8(%d),"
                     "igt:[m.read8(%d),m.read8(%d),m.read8(%d)]}" % (
                         o + f["exists"], o + f["corruptionFlag"],
                         o + f["introPlayed"], o + f["currentArea"],
                         o + f["currentEnergy"], o + f["hasSaved"],
                         o + f["igtHours"], o + f["igtMinutes"], o + f["igtSeconds"]))
    return tab.js("(function(){var m=window.__mzm;if(!m||!m.started)return null;"
                  "return {files:[%s],sramCorrupt:m.read8(%d),"
                  "completionIntroPlayed:m.read8(%d)};})()"
                  % (",".join(parts), S["gSramCorruptFlag"]["addr"],
                     ST["gGameCompletion"]["addr"]
                     + ST["gGameCompletion"]["fields"]["introPlayed"]))


def is_play(s) -> bool:
    return bool(s) and s["gm"] == E["GM_INGAME"] and \
        s["sub"] == E["SUB_GAME_MODE_PLAYING"] and s["demo"] == E["DEMO_STATE_NONE"]


KEY = {"A": 1, "B": 2, "SELECT": 4, "START": 8, "RIGHT": 16, "LEFT": 32,
       "UP": 64, "DOWN": 128, "R": 256, "L": 512}


def wait_ready(tab, timeout: float = 60.0) -> bool:
    """goto() returns once the new document is answering, which is BEFORE the
    page's async wasm init has created window.__mzm. Poll for the real thing."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            if tab.js("!!(window.__mzm && window.__mzm.ready)"):
                return True
        except Exception:
            pass
        time.sleep(0.1)
    return False


def wait_frames(tab, n: int, timeout: float = 30.0) -> int:
    """Block until the PAGE has actually advanced n frames.

    Frames, not wall clock: it is the only way to know the key edges either side
    of this call were separated by the core really running."""
    start = tab.js("window.__mzm.frames()")
    end = time.time() + timeout
    while time.time() < end:
        time.sleep(0.03)
        now = tab.js("window.__mzm.frames()")
        if now - start >= n:
            return now - start
    return tab.js("window.__mzm.frames()") - start


def tap(tab, mask: int, hold: int = 6, gap: int = 10):
    tab.js("window.__mzm.setKeys(%d)" % mask)
    wait_frames(tab, hold)
    tab.js("window.__mzm.setKeys(0)")
    wait_frames(tab, gap)


def drive_to_gameplay(tab, streak_needed=600, budget=9000):
    """Reach gameplay and HOLD it.

    Zero Mission hands you a short GM_INGAME window while the ship lands and
    takes it away again for the arrival cutscene, so first-sighting is not
    arrival. Nothing but A and START is pressed here; START in-game opens the
    map screen, which is how a blind masher ends up oscillating forever."""
    seen, streak, spent = {}, 0, 0
    while spent < budget:
        s = read_state(tab)
        if s is None:
            return None, seen
        seen.setdefault("%d/%d/%d" % (s["gm"], s["sub"], s["demo"]), s["frames"])
        if is_play(s):
            tab.js("window.__mzm.setKeys(0)")
            got = wait_frames(tab, 60)
            spent += got
            streak += got
            if streak >= streak_needed:
                return s, seen
            continue
        streak = 0
        tap(tab, KEY["A"] if (spent // 120) % 2 == 0 else KEY["START"])
        spent += 16
    return None, seen


def drive_to_file_select(tab, budget=4000):
    spent = 0
    while spent < budget:
        s = read_state(tab)
        if s is None:
            return None
        if s["gm"] == E["GM_FILE_SELECT"]:
            tab.js("window.__mzm.setKeys(0)")
            wait_frames(tab, 180)
            return s
        tap(tab, KEY["A"])
        spent += 16
    return None


# ------------------------------------------------------------------- stages

def stage1(tab, base, rom, shots):
    print("\n=== stage 1: boot, play, prove it, and persist the save ===", flush=True)
    assert tab.goto(base + "?stage=1"), "navigation to stage 1 failed"
    assert wait_ready(tab), "the page never finished initialising the core"
    nonce_ok = tab.js("window.__mzm && window.__mzm.nonce && "
                      "location.search.indexOf(window.__mzm.nonce) !== -1")
    check(bool(nonce_ok), "the tab answering is the one we navigated: it echoes "
                          "our per-navigation nonce back")
    check(tab.js("new URLSearchParams(location.search).get('stage')") == "1",
          "stage marker in the page URL is 1")

    cs = tab.js("window.__mzm.canvasSize()")
    info("canvas: attr=%s css=%s" % (cs["attr"], cs["css"]))
    check(cs["css"][0] > 100 and cs["css"][1] > 50,
          "the canvas has a non-zero laid-out size (%dx%d css px) -- a core can "
          "render perfectly into a 0x0 canvas and report success" % tuple(cs["css"]))

    tab.set_file_input("#rom", [str(rom)])
    for _ in range(200):
        if tab.js("window.__mzm.started"):
            break
        time.sleep(0.25)
    check(tab.js("window.__mzm.started"),
          "the ROM was accepted through the page's real <input type=file>")

    hdr = tab.js("window.__mzm.header()")
    info("header: %s" % hdr)
    check(hdr["title"].startswith("ZEROMISSION") and hdr["code"] == "BMXE"
          and hdr["revision"] == 0 and hdr["size"] == 8388608,
          "the core reads back ZEROMISSIONE / BMXE / rev 0 / 8388608 bytes")

    s, seen = drive_to_gameplay(tab)
    info("game modes seen on the way in (gm/sub/demo -> frame): %s" % json.dumps(seen))
    check(s is not None,
          "reached and held 600 unbroken frames of gameplay: the game itself "
          "reports GM_INGAME / SUB_GAME_MODE_PLAYING / DEMO_STATE_NONE")
    if s is None:
        return
    if shots:
        tab.screenshot(shots / "stage1-a-gameplay.png")

    before = read_state(tab)
    xs, ys, poses, inplay, out = set(), set(), set(), 0, 0
    for i in range(30):
        mask = KEY["RIGHT"] if i < 15 else KEY["LEFT"]
        if i % 3 == 0:
            mask |= KEY["A"]
        tab.js("window.__mzm.setKeys(%d)" % mask)
        wait_frames(tab, 20)
        st = read_state(tab)
        if is_play(st):
            inplay += 1
            xs.add(st["x"]); ys.add(st["y"]); poses.add(st["pose"])
        else:
            out += 1
    tab.js("window.__mzm.setKeys(0)")
    after = read_state(tab)
    igt_b = ((before["igt"][0] * 60 + before["igt"][1]) * 60 + before["igt"][2]) * 64 + before["igt"][3]
    igt_a = ((after["igt"][0] * 60 + after["igt"][1]) * 60 + after["igt"][2]) * 64 + after["igt"][3]
    info("movement: samples in gameplay=%d outside=%d distinct x=%d y=%d poses=%d"
         % (inplay, out, len(xs), len(ys), len(poses)))
    info("          x %d -> %d, y %d -> %d, in-game timer %d -> %d ticks"
         % (before["x"], after["x"], before["y"], after["y"], igt_b, igt_a))
    check(len(xs) >= 8 and out == 0,
          "Samus moved under our input: %d distinct x positions over %d samples, "
          "every one of them with the game reporting SUB_GAME_MODE_PLAYING and "
          "gDemoState clear" % (len(xs), inplay))
    check(igt_a > igt_b, "the in-game timer advanced (%d -> %d)" % (igt_b, igt_a))
    if shots:
        tab.screenshot(shots / "stage1-b-moved.png")

    sv = tab.js("window.__mzm.save()")
    info("save media: %s" % sv)
    check(sv["type"] == 1 and sv["mediaSize"] == 32768 and sv["size"] == 32768,
          "mGBA attached 32 KiB SRAM, which is the save media Zero Mission's own "
          "ROM declares (SRAM_V113)")

    # Brightness, three ways. The page's own canvas read and the wasm-side read
    # can both be fooled; the driver's screenshot is the ground truth.
    wl = tab.js("window.__mzm.meanLuma()")
    cl = tab.js("window.__mzm.canvasLuma()")
    shot = (shots or pathlib.Path("/tmp")) / "stage1-luma.png"
    shot.parent.mkdir(parents=True, exist_ok=True)
    tab.screenshot(shot)
    # pngstat.luma takes the crop as FRACTIONS of the image, so convert from the
    # canvas's viewport rect. Cropping matters: page chrome scored as "picture"
    # would let a black emulator pass on the strength of the surrounding UI.
    box = tab.js("(function(){var r=document.getElementById('screen')"
                 ".getBoundingClientRect();"
                 "var vw=window.innerWidth, vh=window.innerHeight;"
                 # clamp: in a short viewport the canvas runs past the fold,
                 # and an unclamped fraction indexes off the end of the image
                 "var c=function(v){return Math.max(0,Math.min(1,v));};"
                 "return [c(r.left/vw), c(r.top/vh), c(r.right/vw), c(r.bottom/vh)];})()")
    drv = pngstat.luma(shot, box)
    info("mean luma  wasm=%.2f  page-canvas=%.2f  driver-screenshot(cropped)=%s"
         % (wl, cl, drv))
    check(drv["mean"] > 1.0 and drv["lit"] > 0.05,
          "the screenshot the BROWSER handed back is not black: cropped to the "
          "canvas rect, mean luma %.2f with %.0f%% of pixels lit"
          % (drv["mean"], drv["lit"] * 100))

    check(tab.js("window.__mzm.audioContexts()") == 0,
          "no AudioContext was ever constructed -- muted here means there is no "
          "audio graph at all, not a gain node at zero")

    urls = tab.requests()
    foreign = [u for u in urls if not u.startswith(base.rsplit("/", 1)[0])
               and not u.startswith("data:") and not u.startswith("blob:")]
    info("network (CDP, not the page's self-report): %d requests, %s"
         % (len(urls), [u.rsplit("/", 1)[-1] for u in urls]))
    check(not foreign, "every request the page made was to its own origin "
                       "(the CSP is what enforces this; this measures it)")
    check(not tab.js("window.__mzmNet.length"),
          "the page's own wrapper logged no blocked cross-origin attempt either")

    # Let the SRAM settle before capturing it. Zero Mission's intro save is a
    # multi-stage write (SramProcessIntroSave runs over several frames), and a
    # fixed wait can capture a half-written save that then survives a reload
    # byte for byte and is still rejected by the game. Waiting for the hash to
    # stop moving is necessary but not sufficient -- stage 2 settles it properly
    # by asking the game's own checksum code.
    stable, prev = 0, None
    for _ in range(60):
        h = tab.js("window.__mzm.save()")["hash"]
        stable = stable + 1 if h == prev else 0
        prev = h
        if stable >= 8:
            break
        tab.drain(0.25)
    info("cartridge SRAM hash settled at %s after %d stable samples" % (prev, stable))
    check(stable >= 8, "the cartridge SRAM stopped changing before we captured "
                       "it, so what gets stored is not a half-finished write")

    # Persist, then prove nothing on the page destroys what was persisted.
    tab.js("window.__mzm.flushSave()", await_promise=True, timeout=30)
    live = tab.js("window.__mzm.save()")["hash"]
    idb_before = tab.js("window.__mzm.idbSaveHash('sram:BMXE')",
                        await_promise=True, timeout=30)
    files_before = tab.js("window.__mzm.idbSave()", await_promise=True, timeout=30)
    info("IndexedDB after flush: %s  (core save hash %d)" % (files_before, live))
    check(idb_before and idb_before["len"] == 32768,
          "32 KiB of cartridge SRAM is in IndexedDB")

    # Deterministic, not a soak. Dispatching the events reproduces what leaving
    # the page runs, without racing the tab's death -- a navigate-away soak
    # passed 8/8 on a page that was provably destroying its save.
    for ev in ("beforeunload", "pagehide", "visibilitychange"):
        tab.js("window.dispatchEvent(new Event('%s'));"
               "document.dispatchEvent(new Event('%s'))" % (ev, ev))
        tab.drain(0.5)
    tab.drain(2.0)
    idb_after = tab.js("window.__mzm.idbSaveHash('sram:BMXE')",
                       await_promise=True, timeout=30)
    files_after = tab.js("window.__mzm.idbSave()", await_promise=True, timeout=30)
    info("IndexedDB after dispatching beforeunload/pagehide/visibilitychange: %s"
         % files_after)
    check(idb_after and idb_after["len"] == 32768,
          "none of the page's unload handlers emptied the save store "
          "(dispatched, not navigated, so there is no race to get lucky with)")
    # Carry the hash read AFTER the dispatch test, not before: the page keeps
    # flushing on its hash-change poll and the game keeps writing SRAM, so the
    # earlier value is already stale by the time stage 2 looks.
    return {"idb_hash": idb_after["hash"] if idb_after else None}


def stage2(tab, base, rom, shots, carried):
    print("\n=== stage 2: real navigation, then does the GAME see the save? ===",
          flush=True)
    assert tab.goto(base + "?stage=2"), "navigation to stage 2 failed"
    assert wait_ready(tab), "the page never finished initialising the core"
    check(tab.js("new URLSearchParams(location.search).get('stage')") == "2",
          "stage marker in the page URL is 2 -- this is not stage 1's report")
    check(not tab.js("window.__mzm.started"),
          "the fresh page has no ROM loaded yet, so nothing is running that "
          "could rewrite the thing we are about to measure")

    idb = tab.js("window.__mzm.idbSaveHash('sram:BMXE')", await_promise=True, timeout=30)
    info("IndexedDB on the fresh load, before any ROM is picked: %s" % idb)
    check(idb and idb["len"] == 32768 and idb["hash"] == carried["idb_hash"],
          "the cartridge SRAM survived a real same-tab navigation byte for byte "
          "(FNV-1a %s)" % (idb["hash"] if idb else None))

    tab.set_file_input("#rom", [str(rom)])
    for _ in range(200):
        if tab.js("window.__mzm.started"):
            break
        time.sleep(0.25)
    check(tab.js("window.__mzm.started"), "the ROM was accepted again")
    rest = tab.js("window.__mzm.restored()")
    info("what the page handed the core before loadROM: %s" % rest)
    check(rest and rest["len"] == 32768 and rest["hash"] == carried["idb_hash"],
          "the page fed the stored SRAM into the core BEFORE loadROM, byte for "
          "byte -- mGBA reads that buffer when it attaches the save media, so "
          "restoring afterwards would be silently ignored")

    s = drive_to_file_select(tab)
    check(s is not None, "drove as far as the game's own file select screen "
                         "(GM_FILE_SELECT)")
    fs = read_files(tab)
    info("the game's own view of the restored SRAM: %s" % json.dumps(fs))
    if shots:
        tab.screenshot(shots / "stage2-file-select.png")
    f0 = (fs or {}).get("files", [{}])[0]

    # This is the part that a byte diff cannot do. A save that survived a reload
    # byte for byte can still be a half-written one, so the question has to be
    # put to the game: SramRead_All -> SramTestFlash sets gSramCorruptFlag, and
    # unk_743a4 -> unk_74574 -> unk_74624 runs Zero Mission's own sanity checks
    # over the restored file and writes the verdict into
    # gSaveFilesInfo[i].corruptionFlag.
    check(fs.get("sramCorrupt") == 0,
          "the game's own SRAM test passed on the restored data "
          "(gSramCorruptFlag == 0)")
    check(f0.get("corruptionFlag") == 0,
          "the game's own per-file validation accepted the restored file "
          "(gSaveFilesInfo[0].corruptionFlag == 0, the verdict unk_74624 writes)")
    # SramWrite_FileInfo only copies introPlayed out of SRAM for a file that
    # unk_743a4 had already marked exists == TRUE, i.e. one whose checksum
    # passed; it then clears `exists` again because this file has no
    # save-station save (hasSaved == FALSE). So introPlayed == 1 here is
    # unreachable unless the game validated our restored bytes and read them.
    check(f0.get("introPlayed") == 1,
          "the game read gameCompletion.introPlayed back OUT of the restored "
          "SRAM -- a field it only copies for a file its own checksum accepted")
    info("note: exists=%s and hasSaved=%s, as expected -- this save was written "
         "by the game's post-intro SramProcessIntroSave, not by a save station, "
         "and area=%s/energy=%s are the defaults the game derives from "
         "introPlayed rather than stored values"
         % (f0.get("exists"), f0.get("hasSaved"), f0.get("area"), f0.get("energy")))
    return fs


def stage3(rom, shots, srv_port, control_files):
    print("\n=== stage 3: the negative control (?nosaveflush=1) ===", flush=True)
    base = f"http://localhost:{srv_port}/index.html"
    ch = cdp.Chrome(window="1024,980")
    try:
        ch.close_other_pages()
        tab = ch.tab("about:blank")
        assert tab.goto(base + "?stage=3&nosaveflush=1"), "navigation failed"
        assert wait_ready(tab), "the page never finished initialising the core"
        logged = False
        tab.set_file_input("#rom", [str(rom)])
        for _ in range(200):
            if tab.js("window.__mzm.started"):
                break
            time.sleep(0.25)
        check(tab.js("window.__mzm.started"), "control arm: ROM accepted")
        # Prove the arms differ. If ?nosaveflush=1 silently did nothing, this
        # control would be the same page as stage 1 and would prove nothing.
        logged = any("save flushing DISABLED" in l for l in tab.console())
        check(logged and tab.js("window.__mzm.save()").get("flushingDisabled") is True,
              "control arm really is a different page: it logged that its save "
              "flushing is disabled")
        s, _ = drive_to_gameplay(tab, streak_needed=300, budget=6000)
        check(s is not None, "control arm reached gameplay too, so the only "
                             "difference between the arms is the save path")
        for ev in ("beforeunload", "pagehide", "visibilitychange"):
            tab.js("window.dispatchEvent(new Event('%s'))" % ev)
        tab.drain(2.0)
        assert tab.goto(base + "?stage=3b&nosaveflush=1")
        assert wait_ready(tab), "the page never finished initialising the core"
        idb = tab.js("window.__mzm.idbSave()", await_promise=True, timeout=30)
        info("control arm IndexedDB after the same journey: %s" % idb)
        check(not idb.get("files"),
              "the control arm persisted NOTHING -- so stage 2's pass is the "
              "save path working, not the test being unable to tell")
        # And ask the game the same question with nothing restored, so stage 2's
        # "the game read it back" has a matching negative.
        tab.set_file_input("#rom", [str(rom)])
        for _ in range(200):
            if tab.js("window.__mzm.started"):
                break
            time.sleep(0.25)
        check(tab.js("window.__mzm.restored()") is None,
              "control arm: nothing was restored into the core before loadROM")
        s2 = drive_to_file_select(tab)
        check(s2 is not None, "control arm reached the file select screen too")
        fs = read_files(tab)
        info("the game's own view with nothing restored: %s" % json.dumps(fs))
        f0 = (fs or {}).get("files", [{}])[0]
        check(f0.get("introPlayed") == 0,
              "with nothing restored the game reports introPlayed=0 on the same "
              "screen -- so stage 2's introPlayed=1 came from our stored bytes")
        if shots:
            tab.screenshot(shots / "stage3-control.png")
    finally:
        ch.kill()


# ---------------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("rom", type=pathlib.Path)
    ap.add_argument("--shots", type=pathlib.Path)
    ap.add_argument("--head", action="store_true")
    a = ap.parse_args()
    if not a.rom.is_file():
        print("no such ROM: %s" % a.rom)
        return 2
    if a.shots:
        a.shots.mkdir(parents=True, exist_ok=True)

    srv = subprocess.Popen([sys.executable, str(PROJECT / "tools" / "serve.py"), "0"],
                           stdout=subprocess.PIPE, text=True)
    line = srv.stdout.readline().strip()
    if not line.startswith("PORT "):
        print("server did not start: %r" % line)
        return 1
    port = int(line.split()[1])
    base = f"http://localhost:{port}/index.html"
    print("serving on %s" % base, flush=True)

    ch = None
    try:
        ch = cdp.Chrome(headless=not a.head, window="1024,980")
        ch.close_other_pages()
        tab = ch.tab("about:blank")
        carried = stage1(tab, base, a.rom.resolve(), a.shots)
        if carried:
            stage2(tab, base, a.rom.resolve(), a.shots, carried)
        errs = [l for l in tab.console() if l.startswith("[exception]")]
        check(not errs, "no uncaught page exceptions")
        ch.kill()
        ch = None
        stage3(a.rom.resolve(), a.shots, port, None)
    finally:
        if ch:
            ch.kill()
        srv.terminate()
        try:
            srv.wait(timeout=5)
        except Exception:
            srv.kill()

    bad = [m for ok, m in results if not ok]
    print("\n%d/%d checks passed" % (len(results) - len(bad), len(results)))
    for m in bad:
        print("  FAILED: " + m)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
