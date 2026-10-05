#!/usr/bin/env python3
"""Does anything on this page destroy the cartridge save when you leave it?

Adapted from ~/decompfleet/games/pokefirered/tools/save_mechanism.py (kept
alongside this file as save_mechanism_pokefirered_reference.py). That session
established the method and why it matters: the user-visible experiment --
navigate away, come back, look -- is a *weak detector*. It passed 8 times out of
8 on a page that was provably emptying its save store, because the real unload
is a race that headless Chrome usually wins. Dispatching the events instead
removes the race and answers the question deterministically.

This page is not EmulatorJS and has no IDBFS mount to tear down, so EmulatorJS's
trap 10 cannot apply here by construction. The point of running it anyway is
that "cannot apply" is a claim, and this measures it.

Two arms, and the control is checked rather than assumed: if ?nosaveflush=1
silently did nothing, both arms would be the same page and agreement between
them would mean nothing.

    python3 tools/save_mechanism.py <rom>
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
PROJECT = HERE.parent
sys.path.insert(0, str(HERE))
import cdp  # noqa: E402

EVENTS = ("beforeunload", "pagehide", "visibilitychange")


def ready(tab, timeout=60.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if tab.js("!!(window.__mzm && window.__mzm.ready)"):
                return True
        except Exception:
            pass
        time.sleep(0.1)
    return False


def run_arm(base, rom, no_flush: bool) -> dict:
    out: dict = {"nosaveflush": no_flush}
    ch = cdp.Chrome(window="1024,980")
    try:
        ch.close_other_pages()
        tab = ch.tab("about:blank")
        url = base + ("?nosaveflush=1" if no_flush else "?nosaveflush=0")
        assert tab.goto(url), "navigation failed"
        assert ready(tab), "page never initialised"
        tab.set_file_input("#rom", [str(rom)])
        for _ in range(240):
            if tab.js("window.__mzm.started"):
                break
            time.sleep(0.25)
        if not tab.js("window.__mzm.started"):
            out["error"] = "core never started"
            return out

        # Let the game get through its boot-time SRAM format and its post-intro
        # save, then wait for the image to stop moving. A fixed wait can capture
        # a half-written save that survives a reload byte for byte and is still
        # rejected by the game.
        prev, stable = None, 0
        for _ in range(200):
            h = tab.js("window.__mzm.save()")["hash"]
            stable = stable + 1 if h == prev else 0
            prev = h
            if stable >= 8:
                break
            tab.drain(0.25)
        out["settled_hash"] = prev
        out["settled_samples"] = stable

        # The control: the arm must prove it is the arm it says it is.
        out["flush_disabled_logged"] = any(
            "save flushing DISABLED" in line for line in tab.console())
        out["flush_disabled_reported"] = tab.js("window.__mzm.save()")["flushingDisabled"]

        tab.js("window.__mzm.flushSave()", await_promise=True, timeout=30)
        tab.drain(1.0)
        out["before"] = tab.js("window.__mzm.idbSave()", await_promise=True, timeout=30)

        for ev in EVENTS:
            tab.js("window.dispatchEvent(new Event('%s'));"
                   "document.dispatchEvent(new Event('%s'))" % (ev, ev))
            tab.drain(0.5)
        tab.drain(2.0)
        out["after_dispatch"] = tab.js("window.__mzm.idbSave()",
                                       await_promise=True, timeout=30)

        # And the user-visible version too, for comparison. Navigate away in the
        # SAME tab: closing the tab over CDP can skip the unload handlers
        # entirely, so it would not be testing what a person does.
        assert tab.goto(base + "?after=1" + ("&nosaveflush=1" if no_flush else ""))
        assert ready(tab)
        out["after_navigate"] = tab.js("window.__mzm.idbSave()",
                                       await_promise=True, timeout=30)
    finally:
        ch.kill()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("rom", type=pathlib.Path)
    a = ap.parse_args()
    if not a.rom.is_file():
        print("no such ROM: %s" % a.rom)
        return 2

    srv = subprocess.Popen([sys.executable, str(HERE / "serve.py"), "0"],
                           stdout=subprocess.PIPE, text=True)
    line = srv.stdout.readline().strip()
    if not line.startswith("PORT "):
        print("server did not start: %r" % line)
        return 1
    base = "http://localhost:%s/index.html" % line.split()[1]

    try:
        normal = run_arm(base, a.rom.resolve(), no_flush=False)
        control = run_arm(base, a.rom.resolve(), no_flush=True)
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=5)
        except Exception:
            srv.kill()

    def files(d, k):
        return (d.get(k) or {}).get("files", {})

    print()
    print("                                   normal page      ?nosaveflush=1")
    print("  arm says flushing is disabled    %-16s %s"
          % (normal.get("flush_disabled_reported"), control.get("flush_disabled_reported")))
    print("  store after flush                %-16s %s"
          % (files(normal, "before"), files(control, "before")))
    print("  store after dispatching %-9s%-16s %s"
          % ("unload", files(normal, "after_dispatch"), files(control, "after_dispatch")))
    print("  store after a real navigation    %-16s %s"
          % (files(normal, "after_navigate"), files(control, "after_navigate")))

    ok = True
    if normal.get("flush_disabled_reported") is not False or \
            control.get("flush_disabled_reported") is not True:
        print("\n  !! the two arms are not actually different -- the control is broken")
        ok = False
    if not files(normal, "before"):
        print("\n  !! the normal arm never stored anything to begin with")
        ok = False
    if not files(normal, "after_dispatch"):
        print("\n  !! DESTROYED: dispatching the unload events emptied the store")
        ok = False
    if not files(normal, "after_navigate"):
        print("\n  !! DESTROYED: a real navigation emptied the store")
        ok = False
    if files(control, "after_navigate"):
        print("\n  !! the control arm persisted something; it should persist nothing")
        ok = False

    out = PROJECT / "out"
    out.mkdir(exist_ok=True)
    (out / "save_mechanism.json").write_text(
        json.dumps({"normal": normal, "control": control}, indent=2))
    print("\n  %s" % ("no unload path on this page destroys the save, and the "
                      "control arm confirms the test can tell"
                      if ok else "see the failures above"))
    print("  written to %s" % (out / "save_mechanism.json"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
