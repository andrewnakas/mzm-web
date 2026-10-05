/* Core-level smoke test, outside the browser.
 *
 * Isolates "does the mGBA shim work" from "does the page work". Drives the ROM
 * with a state machine that reads the game's own IWRAM, at addresses taken from
 * the symbol table of the matching build (web/gen/symbols.json), and only calls
 * the run "gameplay" when the game says so:
 *
 *     gMainGameMode == GM_INGAME && gSubGameMode1 == SUB_GAME_MODE_PLAYING
 *     && gDemoState == DEMO_STATE_NONE
 *
 * Zero Mission has a title-screen attract demo that is screenshot-identical to
 * playing, so a picture cannot settle this; gDemoState can.
 *
 * Audio is never drained and no audio device exists in node, so this is silent
 * by construction.
 *
 *   node core-smoke.mjs <rom path> <symbols.json>   [VJ_DUMP=dir for PPM frames]
 */

import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const createGBA = require(resolve(here, 'core-node.js'));

const romPath = process.argv[2];
const symPath = process.argv[3] || resolve(here, '..', 'gen', 'symbols.json');
if (!romPath) {
	console.error('usage: node core-smoke.mjs <rom> <symbols.json>');
	process.exit(2);
}
const sym = JSON.parse(readFileSync(symPath, 'utf8'));
const rom = readFileSync(romPath);

let failures = 0;
const fail = (m) => { console.log('FAIL ' + m); failures++; };
const pass = (m) => { console.log('PASS ' + m); };

const M = await createGBA();
const a = {
	init: M.cwrap('vj_init', 'number', []),
	loadRom: M.cwrap('vj_load_rom', 'number', ['number', 'number']),
	run: M.cwrap('vj_run_frame', null, []),
	keys: M.cwrap('vj_set_keys', null, ['number']),
	frameCounter: M.cwrap('vj_frame_counter', 'number', []),
	opaque: M.cwrap('vj_opaque', null, []),
	luma: M.cwrap('vj_mean_luma', 'number', []),
	frameHash: M.cwrap('vj_frame_hash', 'number', []),
	r8: M.cwrap('vj_read8', 'number', ['number']),
	r16: M.cwrap('vj_read16', 'number', ['number']),
	saveHash: M.cwrap('vj_save_hash', 'number', []),
	saveBuffer: M.cwrap('vj_save_buffer', 'number', []),
	saveSize: M.cwrap('vj_save_size', 'number', []),
	saveType: M.cwrap('vj_save_type', 'number', []),
	saveMediaSize: M.cwrap('vj_save_media_size', 'number', []),
	stateSize: M.cwrap('vj_state_size', 'number', []),
	stateSave: M.cwrap('vj_state_save', 'number', []),
	stateLoad: M.cwrap('vj_state_load', 'number', ['number', 'number']),
	title: M.cwrap('vj_game_title', 'string', []),
	code: M.cwrap('vj_game_code', 'string', []),
	revision: M.cwrap('vj_rom_revision', 'number', []),
	sampleRate: M.cwrap('vj_sample_rate', 'number', []),
	width: M.cwrap('vj_width', 'number', []),
	height: M.cwrap('vj_height', 'number', []),
	framebuffer: M.cwrap('vj_framebuffer', 'number', [])
};

if (!a.init()) { fail('vj_init'); process.exit(1); }
console.log('init ok, sampleRate=' + a.sampleRate());

{
	const p = M._malloc(rom.length);
	M.HEAPU8.set(rom, p);
	const ok = a.loadRom(p, rom.length);
	M._free(p);
	if (!ok) { fail('vj_load_rom'); process.exit(1); }
}
console.log('rom: title="' + a.title() + '" code=' + a.code() +
	' headerRevision=' + a.revision() + ' bytes=' + rom.length);
if (a.code() !== 'BMXE') {
	fail('expected game code BMXE (Zero Mission, USA), got ' + a.code());
}

/* ---------------------------------------------------- reading game state */
const S = sym.scalars, ST = sym.structs, E = sym.enums;
const sc = (n) => (S[n].width === 1 ? a.r8(S[n].addr) : a.r16(S[n].addr));
const fld = (s, f, w) => {
	const addr = ST[s].addr + ST[s].fields[f];
	return w === 1 ? a.r8(addr) : a.r16(addr);
};
const igt = () => [0, 1, 2, 3].map((i) =>
	a.r8(ST.gInGameTimer.addr + ST.gInGameTimer.fields[['hours', 'minutes', 'seconds', 'frames'][i]]));
const igtFrames = () => { const t = igt(); return ((t[0] * 60 + t[1]) * 60 + t[2]) * 64 + t[3]; };
const stateNow = () => ({
	gm: sc('gMainGameMode'), sub: sc('gSubGameMode1'), demo: sc('gDemoState'),
	x: fld('gSamusData', 'xPosition', 2), y: fld('gSamusData', 'yPosition', 2),
	pose: fld('gSamusData', 'pose', 1), igt: igtFrames(),
	energy: fld('gEquipment', 'currentEnergy', 2)
});
const isGameplay = (s) => s.gm === E.GM_INGAME && s.sub === E.SUB_GAME_MODE_PLAYING &&
	s.demo === E.DEMO_STATE_NONE;

const KEY = { A: 1, B: 2, SELECT: 4, START: 8, RIGHT: 16, LEFT: 32, UP: 64, DOWN: 128, R: 256, L: 512 };

/* ------------------------------------------------------------ frame dumps */
const dumpDir = process.env.VJ_DUMP;
if (dumpDir) mkdirSync(dumpDir, { recursive: true });
const W = a.width(), H = a.height(), fbPtr = a.framebuffer();
function dump(tag) {
	if (!dumpDir) return;
	a.opaque();
	const rgba = M.HEAPU8.subarray(fbPtr, fbPtr + W * H * 4);
	const px = Buffer.allocUnsafe(W * H * 3);
	for (let i = 0; i < W * H; i++) {
		px[i * 3] = rgba[i * 4]; px[i * 3 + 1] = rgba[i * 4 + 1]; px[i * 3 + 2] = rgba[i * 4 + 2];
	}
	writeFileSync(`${dumpDir}/${tag}.ppm`,
		Buffer.concat([Buffer.from(`P6\n${W} ${H}\n255\n`, 'ascii'), px]));
}

/* -------------------------------------------- phase 1: get into the game
 * Press only A and START, with real frames between the press and the release
 * so each edge is seen. Stop the moment the game reports gameplay: pressing
 * START once in-game opens the map screen, which is why a blind masher ends up
 * oscillating between GM_INGAME and GM_MAP_SCREEN forever.
 */
function tap(mask, hold = 6, gap = 10) {
	a.keys(mask);
	for (let i = 0; i < hold; i++) a.run();
	a.keys(0);
	for (let i = 0; i < gap; i++) a.run();
}

/* "Reached gameplay" has to mean reached and STAYED, not touched for a frame.
 * Zero Mission hands you a brief GM_INGAME window while the ship lands and then
 * takes it away again for the arrival cutscene, so a first-sighting test would
 * start the movement phase inside a cutscene and then correctly report that
 * nothing moved. Require an unbroken run of gameplay frames with no input. */
const seen = new Map();
let reached = -1;
const STREAK_NEEDED = 600;   // 10 s: long enough to clear the ship-landing window
                             // and the arrival cutscene that follows it
const BOOT_BUDGET = 9000;   // 150 s of game time
let streak = 0;
for (let f = 0; f < BOOT_BUDGET; f++) {
	const s = stateNow();
	const k = `${s.gm}/${s.sub}/${s.demo}`;
	if (!seen.has(k)) { seen.set(k, a.frameCounter()); }
	if (isGameplay(s)) {
		streak++;
		if (streak >= STREAK_NEEDED) { reached = a.frameCounter(); break; }
		a.keys(0);
		a.run();
		continue;
	}
	streak = 0;
	tap(f % 120 < 60 ? KEY.A : KEY.START);
	f += 16;
}
console.log('game modes seen on the way in (gm/sub/demo -> frame): ' +
	JSON.stringify(Object.fromEntries(seen)));
if (reached < 0) {
	fail('never reached ' + STREAK_NEEDED + ' unbroken frames of gameplay ' +
		'(gMainGameMode=GM_INGAME, gSubGameMode1=PLAYING, gDemoState=NONE) ' +
		'within ' + BOOT_BUDGET + ' frames');
} else {
	pass('reached gameplay by frame ' + reached + ' and held it for ' +
		STREAK_NEEDED + ' unbroken frames — the game itself reports ' +
		'GM_INGAME / SUB_GAME_MODE_PLAYING / DEMO_STATE_NONE');
}
dump('a-gameplay-reached');

/* The attract demo must have been avoided, not merely not-looked-at. */
const sawDemo = [...seen.keys()].some((k) => k.endsWith('/' + E.DEMO_STATE_PLAYING));
console.log('attract demo (gDemoState==DEMO_STATE_PLAYING) seen during boot: ' +
	(sawDemo ? 'yes' : 'no') + ' — the gate excludes it either way');

/* ------------------------------------- phase 2: move, and prove it moved
 * Only direction keys and A (jump) from here: START opens the map screen and
 * would leave gameplay. Movement is judged on the game's own Samus coordinates
 * and in-game timer, not on the picture.
 */
let before = stateNow();
const xs = new Set(), ys = new Set(), poses = new Set();
let framesInGameplay = 0, framesOutside = 0;
const MOVE_FRAMES = 900;
for (let i = 0; i < MOVE_FRAMES; i++) {
	// Run right, with a jump every half second.
	let m = KEY.RIGHT;
	if (i % 30 < 8) m |= KEY.A;
	if (i > 450) { m = KEY.LEFT; if (i % 30 < 8) m |= KEY.A; }
	a.keys(m);
	a.run();
	const s = stateNow();
	if (isGameplay(s)) {
		framesInGameplay++; xs.add(s.x); ys.add(s.y); poses.add(s.pose);
	} else {
		framesOutside++;
	}
	if (i === Math.floor(MOVE_FRAMES / 2)) dump('b-mid-movement');
}
a.keys(0);
const after = stateNow();
dump('c-after-movement');

console.log('movement: frames in gameplay=' + framesInGameplay + ' outside=' + framesOutside);
console.log('  x before=' + before.x + ' after=' + after.x + ' distinct x=' + xs.size);
console.log('  y before=' + before.y + ' after=' + after.y + ' distinct y=' + ys.size);
console.log('  distinct Samus poses=' + poses.size +
	' igt ' + before.igt + ' -> ' + after.igt + ' (frames)');

if (framesInGameplay < MOVE_FRAMES * 0.8) {
	fail('spent only ' + framesInGameplay + '/' + MOVE_FRAMES +
		' frames in gameplay; the run left the playing state');
}
if (xs.size < 10) {
	fail('Samus’s x position took only ' + xs.size +
		' distinct values over ' + MOVE_FRAMES + ' frames — that is not movement');
} else {
	pass('Samus moved: ' + xs.size + ' distinct x positions, ' + ys.size +
		' distinct y, ' + poses.size + ' distinct poses, all while the game ' +
		'reported SUB_GAME_MODE_PLAYING with the demo flag clear');
}
if (after.igt <= before.igt) {
	fail('the in-game timer did not advance (' + before.igt + ' -> ' + after.igt + ')');
} else {
	pass('in-game timer advanced ' + (after.igt - before.igt) + ' ticks');
}

/* ------------------------------------------------------- picture is there */
a.opaque();
const luma = a.luma();
console.log('mean luma at end of movement: ' + luma.toFixed(2));
if (!(luma > 1.0)) {
	fail('the frame is essentially black at the end of the run (luma ' + luma.toFixed(3) + ')');
} else {
	pass('non-black frame, mean luma ' + luma.toFixed(2));
}

/* --------------------------------------------------------- the save media */
console.log('save media: type=' + a.saveType() + ' (1 == GBA_SAVEDATA_SRAM)' +
	' mediaSize=' + a.saveMediaSize() + ' shimBuffer=' + a.saveSize() +
	' hash=' + a.saveHash());
if (a.saveType() !== 1) {
	fail('mGBA attached save type ' + a.saveType() + ', expected 1 (SRAM). ' +
		'Zero Mission’s own signature in the ROM is SRAM_V113.');
} else if (a.saveMediaSize() !== 32768) {
	fail('save media is ' + a.saveMediaSize() + ' bytes, expected 32768');
} else {
	pass('mGBA attached 32 KiB SRAM, which is what the cartridge declares');
}

/* ------------------------------------------------------ savestate round trip
 * Exact, not approximate: snapshot, render one frame from the snapshot to get a
 * reference hash, diverge for 240 frames of input, restore and render one frame
 * again. The restored hash must equal the reference exactly, and the diverged
 * hash must differ from it — otherwise a core that silently ignored both the
 * input and the restore would pass. */
a.opaque();
const sSize = a.stateSize();
const sPtr = a.stateSave();
if (!sPtr || sSize <= 0) {
	fail('savestate capture (size=' + sSize + ')');
} else {
	const snap = Uint8Array.from(M.HEAPU8.subarray(sPtr, sPtr + sSize));
	a.keys(0); a.run(); a.opaque();
	const hRef = a.frameHash();
	for (let i = 0; i < 240; i++) {
		a.keys(i % 40 < 20 ? KEY.RIGHT : KEY.LEFT);
		a.run();
	}
	a.keys(0);
	a.opaque();
	const hAfter = a.frameHash();
	const rp = M._malloc(sSize);
	M.HEAPU8.set(snap, rp);
	const ok = a.stateLoad(rp, sSize);
	M._free(rp);
	a.keys(0); a.run(); a.opaque();
	const hBack = a.frameHash();
	console.log('savestate: size=' + sSize + ' loaded=' + ok +
		' hash ref=' + hRef + ' +240=' + hAfter + ' restored=' + hBack);
	if (!ok) fail('savestate load returned false');
	else if (hAfter === hRef) fail('inconclusive: the picture never changed over the ' +
		'divergence window, so the round trip proves nothing');
	else if (hBack !== hRef) fail('savestate did not restore the exact frame ' +
		'(' + hBack + ' != ' + hRef + ')');
	else pass('savestate round trip reproduced the exact frame after 240 diverging frames');
}

/* ------------------------------------- the save, judged by the game itself
 * A save that survives a reload byte for byte can still be a half-written one,
 * so the byte comparison is not the test. Snapshot the cartridge SRAM, hand it
 * back on a fresh load, drive only as far as the game's file select screen, and
 * read the verdict Zero Mission's own code writes:
 *   SramRead_All -> SramTestFlash            -> gSramCorruptFlag
 *   unk_743a4 -> unk_74574 -> unk_74624      -> gSaveFilesInfo[i].corruptionFlag
 * SramWrite_FileInfo only copies gameCompletion.introPlayed out of SRAM for a
 * file that passed those checks, so introPlayed == 1 cannot be faked by a
 * partial write. The blank-SRAM arm below is the control.
 */
const fileAt = (i, f) => {
	const addr = ST.gSaveFilesInfo.addr + i * ST.gSaveFilesInfo.stride +
		ST.gSaveFilesInfo.fields[f];
	return f === 'currentEnergy' || f === 'maxEnergy' ? a.r16(addr) : a.r8(addr);
};
const verdict = () => ({
	sramCorrupt: a.r8(S.gSramCorruptFlag.addr),
	completionIntroPlayed: a.r8(ST.gGameCompletion.addr + ST.gGameCompletion.fields.introPlayed),
	file0: ['exists', 'corruptionFlag', 'introPlayed', 'hasSaved', 'currentArea',
		'currentEnergy'].reduce((o, f) => (o[f] = fileAt(0, f), o), {})
});

function reboot(bytes) {
	if (bytes) {
		const sp = M._malloc(bytes.length);
		M.HEAPU8.set(bytes, sp);
		M.cwrap('vj_save_restore', 'number', ['number', 'number'])(sp, bytes.length);
		M._free(sp);
	}
	const rp = M._malloc(rom.length);
	M.HEAPU8.set(rom, rp);
	a.loadRom(rp, rom.length);
	M._free(rp);
	for (let i = 0; i < 4000; i++) {
		const ph = i % 60;
		if (ph === 0) a.keys(KEY.A); else if (ph === 8) a.keys(0);
		else if (ph === 30) a.keys(KEY.START); else if (ph === 38) a.keys(0);
		a.run();
		if (sc('gMainGameMode') === E.GM_FILE_SELECT) {
			a.keys(0);
			for (let j = 0; j < 180; j++) a.run();
			return true;
		}
	}
	a.keys(0);
	return false;
}

{
	// Settle first: the intro save is a multi-stage write and capturing it
	// mid-flight is exactly how a byte-identical-but-rejected save happens.
	let prev = -1, stable = 0;
	for (let i = 0; i < 600 && stable < 120; i++) {
		a.run();
		const h = a.saveHash();
		stable = h === prev ? stable + 1 : 0;
		prev = h;
	}
	console.log('cartridge SRAM settled: hash=' + prev + ' stable for ' + stable + ' frames');
	const sb = a.saveBuffer(), sz = a.saveSize();
	const snap = new Uint8Array(M.HEAPU8.subarray(sb, sb + sz));

	const okR = reboot(snap);
	const vR = verdict();
	console.log('restored-SRAM boot -> file select: ' + okR + ' ' + JSON.stringify(vR));

	const okB = reboot(new Uint8Array(sz));
	const vB = verdict();
	console.log('blank-SRAM boot    -> file select: ' + okB + ' ' + JSON.stringify(vB));

	// Third arm: hand back the SAME save with its middle scrambled. If the
	// game's validation were inert, this would sail through exactly like the
	// good one -- which is the failure mode a byte diff cannot see. (Both of the
	// first two arms report corruptionFlag 0, because the blank arm gets
	// reformatted into a valid-but-empty file at boot, so this arm is what shows
	// the check is live at all.)
	const bad = Uint8Array.from(snap);
	for (let i = 0x40; i < 0x4000; i++) bad[i] ^= 0xFF;
	const okC = reboot(bad);
	const vC = verdict();
	console.log('corrupted-SRAM boot-> file select: ' + okC + ' ' + JSON.stringify(vC));

	if (!okR || !okB) {
		fail('could not reach the file select screen on one of the two arms');
	} else if (vR.sramCorrupt !== 0 || vR.file0.corruptionFlag !== 0) {
		fail("the game rejected the restored SRAM (gSramCorruptFlag=" + vR.sramCorrupt +
			', corruptionFlag=' + vR.file0.corruptionFlag + ')');
	} else if (vR.file0.introPlayed !== 1) {
		fail('the game did not read introPlayed back out of the restored SRAM');
	} else if (vB.file0.introPlayed !== 0) {
		fail('the blank-SRAM control also reports introPlayed=1, so the ' +
			'restored arm proves nothing');
	} else if (!okC) {
		fail('the corrupted arm never reached the file select screen');
	} else if (vC.file0.introPlayed === 1 && vC.file0.corruptionFlag === 0 &&
			vC.sramCorrupt === 0) {
		fail('a deliberately scrambled save was accepted exactly like the good ' +
			'one, so these checks cannot tell a complete save from a broken one');
	} else {
		pass("Zero Mission's own checks accepted the round-tripped SRAM " +
			'(gSramCorruptFlag=0, corruptionFlag=0) and read gameCompletion.' +
			'introPlayed back out of it; the blank arm reports 0 and the ' +
			'deliberately scrambled arm is rejected (corruptionFlag=' +
			vC.file0.corruptionFlag + ', introPlayed=' + vC.file0.introPlayed + ')');
	}
}

console.log('frames run (core counter): ' + a.frameCounter());
if (failures) { console.log('\n' + failures + ' FAILURE(S)'); process.exitCode = 1; }
else { console.log('\nall core checks passed'); }
