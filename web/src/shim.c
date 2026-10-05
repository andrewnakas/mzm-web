/* Metroid: Zero Mission browser target — mGBA mCore shim.
 *
 * Adapted from the pokeruby fleet target's shim. Exposes a small C surface to
 * JS: no SDL, no filesystem, no audio device. The page owns the canvas and
 * (only if the user asks) the AudioContext. The ROM arrives as bytes from an
 * <input type=file> and is never written anywhere but linear memory.
 *
 * Differences from the pokeruby shim, all MZM-specific:
 *   - the save media is 32 KiB SRAM (the cart's own signature is "SRAM_V113"),
 *     not 128 KiB flash;
 *   - vj_read8/16/32 expose the bus so the harness can gate "played" on the
 *     game's own IWRAM state, read at addresses taken from the symbol table of
 *     the matching build;
 *   - vj_save_type/vj_save_media_size report what mGBA actually attached, so
 *     "32 KiB SRAM" is an assertion rather than an assumption.
 */

#include <mgba/core/core.h>
#include <mgba/core/config.h>
#include <mgba/core/log.h>
#include <mgba/gba/core.h>
#include <mgba/internal/gba/gba.h>
#include <mgba/internal/gba/savedata.h>
#include <mgba-util/vfs.h>
#include <mgba-util/audio-buffer.h>
#include <mgba-util/image.h>

#include <emscripten.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

#define VID_W 240
#define VID_H 160

/* Metroid: Zero Mission is SRAM, 32 KiB. The size is exact, not "big enough":
 * handing mGBA an oversized buffer makes it pick the wrong save media. */
#define SAVE_BYTES (32 * 1024)

static struct mCore* core = NULL;
static mColor* vbuf = NULL;

/* The core keeps referencing the ROM VFile for the whole session, so this copy
 * has to outlive vj_load_rom(). Same for the save buffer. */
static uint8_t* rom = NULL;
static size_t romLen = 0;
static struct VFile* romVf = NULL;

static uint8_t* save = NULL;
static struct VFile* saveVf = NULL;

static int16_t* abuf = NULL;   /* interleaved stereo, drained by JS */
static size_t abufFrames = 0;
static unsigned sampleRate = 0;

static uint8_t* state = NULL;
static size_t stateLen = 0;

static int loaded = 0;

/* ---------------------------------------------------------------- lifecycle */

/* mGBA's default logger prints every BIOS SWI and DMA to stdout, which in a
 * browser means thousands of console lines per second. Swallow everything;
 * vj_* return values are the diagnostic surface. */
static void _quietLog(struct mLogger* logger, int category, enum mLogLevel level,
                      const char* format, va_list args) {
	UNUSED(logger);
	UNUSED(category);
	UNUSED(level);
	UNUSED(format);
	UNUSED(args);
}

static struct mLogger quietLogger = { .log = _quietLog, .filter = NULL };

EMSCRIPTEN_KEEPALIVE
int vj_init(void) {
	if (core) {
		return 1;
	}
	mLogSetDefaultLogger(&quietLogger);
	core = GBACoreCreate();
	if (!core) {
		return 0;
	}
	if (!core->init(core)) {
		core->deinit(core);
		core = NULL;
		return 0;
	}
	/* core->config holds hash tables that mCore reads during reset()
	 * (_GBACoreReset -> mCoreConfigGetBoolValue). GBACoreCreate does not
	 * initialise them, so without this reset() walks a garbage table and traps
	 * on an out-of-bounds read. Nothing is loaded from or written to disk --
	 * mCoreConfigLoad is deliberately not called. */
	mCoreConfigInit(&core->config, "mzm-web");
	core->loadConfig(core, &core->config);

	vbuf = calloc(VID_W * VID_H, sizeof(mColor));
	core->setVideoBuffer(core, vbuf, VID_W);
	core->setAudioBufferSize(core, 1024);
	sampleRate = core->audioSampleRate(core);
	abufFrames = 4096;
	abuf = calloc(abufFrames * 2, sizeof(int16_t));
	return 1;
}

/* Copies, so JS may free its own view afterwards. */
EMSCRIPTEN_KEEPALIVE
int vj_load_rom(const uint8_t* data, int size) {
	if (!core || size <= 0) {
		return 0;
	}
	if (loaded) {
		core->unloadROM(core);
		loaded = 0;
	}
	free(rom);
	rom = malloc((size_t) size);
	if (!rom) {
		return 0;
	}
	memcpy(rom, data, (size_t) size);
	romLen = (size_t) size;

	romVf = VFileFromMemory(rom, romLen);
	if (!romVf || !core->loadROM(core, romVf)) {
		return 0;
	}

	if (!save) {
		save = calloc(SAVE_BYTES, 1);
	}
	saveVf = VFileFromMemory(save, SAVE_BYTES);
	core->loadSave(core, saveVf);

	core->reset(core);
	loaded = 1;
	return 1;
}

EMSCRIPTEN_KEEPALIVE
int vj_loaded(void) {
	return loaded;
}

EMSCRIPTEN_KEEPALIVE
void vj_reset(void) {
	if (core && loaded) {
		core->reset(core);
	}
}

/* ------------------------------------------------------------------- frames */

EMSCRIPTEN_KEEPALIVE
void vj_run_frame(void) {
	if (core && loaded) {
		core->runFrame(core);
	}
}

EMSCRIPTEN_KEEPALIVE
mColor* vj_framebuffer(void) {
	return vbuf;
}

EMSCRIPTEN_KEEPALIVE
int vj_width(void) { return VID_W; }

EMSCRIPTEN_KEEPALIVE
int vj_height(void) { return VID_H; }

EMSCRIPTEN_KEEPALIVE
int vj_frame_counter(void) {
	return (core && loaded) ? (int) core->frameCounter(core) : -1;
}

/* mGBA leaves alpha at 0 in some paths; ImageData needs it opaque. Done here
 * rather than in JS so the page can hand the buffer straight to putImageData. */
EMSCRIPTEN_KEEPALIVE
void vj_opaque(void) {
	uint32_t* p = (uint32_t*) vbuf;
	for (int i = 0; i < VID_W * VID_H; ++i) {
		p[i] |= 0xFF000000u;
	}
}

/* -------------------------------------------------------------------- input */

EMSCRIPTEN_KEEPALIVE
void vj_set_keys(int keys) {
	if (core && loaded) {
		core->setKeys(core, (uint32_t) keys);
	}
}

/* -------------------------------------------------------------------- audio */

EMSCRIPTEN_KEEPALIVE
int vj_sample_rate(void) { return (int) sampleRate; }

EMSCRIPTEN_KEEPALIVE
int16_t* vj_audio_buffer(void) { return abuf; }

/* Returns frames (sample pairs) written into vj_audio_buffer(). JS only calls
 * this when the user has unmuted; otherwise the buffer is simply never drained
 * and mGBA's ring overwrites itself, which costs nothing. */
EMSCRIPTEN_KEEPALIVE
int vj_drain_audio(int maxFrames) {
	if (!core || !loaded) {
		return 0;
	}
	if (maxFrames > (int) abufFrames) {
		maxFrames = (int) abufFrames;
	}
	struct mAudioBuffer* ab = core->getAudioBuffer(core);
	if (!ab) {
		return 0;
	}
	size_t got = mAudioBufferRead(ab, abuf, (size_t) maxFrames);
	return (int) got;
}

/* --------------------------------------------------------- cartridge SRAM */

EMSCRIPTEN_KEEPALIVE
uint8_t* vj_save_buffer(void) { return save; }

EMSCRIPTEN_KEEPALIVE
int vj_save_size(void) { return SAVE_BYTES; }

/* What mGBA actually attached, as opposed to what this file assumed. Values are
 * enum GBASavedataType in mgba/gba/interface.h: -1 AUTODETECT, 0 FORCE_NONE,
 * 1 SRAM, 2 FLASH512, 3 FLASH1M, 4 EEPROM, 5 EEPROM512, 6 SRAM512. It stays at
 * AUTODETECT until the game first touches the save region, so read it after the
 * game has run, not straight after loadROM. Zero Mission must report 1 (SRAM). */
EMSCRIPTEN_KEEPALIVE
int vj_save_type(void) {
	if (!core || !loaded || !core->board) {
		return -1;
	}
	struct GBA* gba = (struct GBA*) core->board;
	return (int) gba->memory.savedata.type;
}

EMSCRIPTEN_KEEPALIVE
int vj_save_media_size(void) {
	if (!core || !loaded || !core->board) {
		return -1;
	}
	struct GBA* gba = (struct GBA*) core->board;
	return (int) GBASavedataSize(&gba->memory.savedata);
}

/* Cheap change detector so the page can persist only when the cartridge save
 * actually moved, instead of writing 32 KiB to IndexedDB on a timer. */
EMSCRIPTEN_KEEPALIVE
unsigned vj_save_hash(void) {
	if (!save) {
		return 0;
	}
	unsigned h = 2166136261u;
	for (int i = 0; i < SAVE_BYTES; ++i) {
		h = (h ^ save[i]) * 16777619u;
	}
	return h;
}

/* Must be called before vj_load_rom(): mGBA reads the buffer when the save
 * media is attached. */
EMSCRIPTEN_KEEPALIVE
int vj_save_restore(const uint8_t* data, int size) {
	if (!save) {
		save = calloc(SAVE_BYTES, 1);
	}
	if (!data || size <= 0) {
		return 0;
	}
	if (size > SAVE_BYTES) {
		size = SAVE_BYTES;
	}
	memcpy(save, data, (size_t) size);
	return size;
}

/* ------------------------------------------------------------- savestates */

EMSCRIPTEN_KEEPALIVE
int vj_state_size(void) {
	return (core && loaded) ? (int) core->stateSize(core) : 0;
}

EMSCRIPTEN_KEEPALIVE
uint8_t* vj_state_save(void) {
	if (!core || !loaded) {
		return NULL;
	}
	size_t n = core->stateSize(core);
	if (n != stateLen) {
		free(state);
		state = malloc(n);
		stateLen = n;
	}
	if (!core->saveState(core, state)) {
		return NULL;
	}
	return state;
}

EMSCRIPTEN_KEEPALIVE
int vj_state_load(const uint8_t* data, int size) {
	if (!core || !loaded || !data) {
		return 0;
	}
	if ((size_t) size != core->stateSize(core)) {
		return 0;
	}
	return core->loadState(core, data) ? 1 : 0;
}

/* ------------------------------------------------------------ bus readback */

/* The harness gates "played" on the game's own state, at addresses taken from
 * the symbol table of the matching build (see tools/gen_symbols.py). It never
 * guesses an address and never reads one off a wiki. */
EMSCRIPTEN_KEEPALIVE
int vj_read8(unsigned addr) {
	return (core && loaded) ? (int) core->busRead8(core, addr) : -1;
}

EMSCRIPTEN_KEEPALIVE
int vj_read16(unsigned addr) {
	return (core && loaded) ? (int) core->busRead16(core, addr) : -1;
}

EMSCRIPTEN_KEEPALIVE
unsigned vj_read32(unsigned addr) {
	return (core && loaded) ? core->busRead32(core, addr) : 0u;
}

/* ------------------------------------------------------------------ probes */

/* Mean luma over the frame. Tells "the core is running" from "the core is
 * running and something is actually on screen" — a black frame ticks fine. */
EMSCRIPTEN_KEEPALIVE
double vj_mean_luma(void) {
	if (!vbuf) {
		return -1.0;
	}
	const uint8_t* p = (const uint8_t*) vbuf;
	double acc = 0.0;
	for (int i = 0; i < VID_W * VID_H; ++i) {
		acc += 0.2126 * p[i * 4 + 0] + 0.7152 * p[i * 4 + 1] + 0.0722 * p[i * 4 + 2];
	}
	return acc / (VID_W * VID_H);
}

/* FNV-1a of the visible frame, for comparing two frames exactly (savestate
 * round-trip checks). */
EMSCRIPTEN_KEEPALIVE
unsigned vj_frame_hash(void) {
	if (!vbuf) {
		return 0;
	}
	const uint8_t* p = (const uint8_t*) vbuf;
	unsigned h = 2166136261u;
	for (int i = 0; i < VID_W * VID_H * 4; ++i) {
		h = (h ^ p[i]) * 16777619u;
	}
	return h;
}

/* Cartridge header, so the page can say which ROM it got (and refuse one that
 * is not Zero Mission) without JS parsing the header itself. */
EMSCRIPTEN_KEEPALIVE
const char* vj_game_title(void) {
	static char title[17];
	memset(title, 0, sizeof(title));
	if (rom && romLen >= 0xB0) {
		memcpy(title, rom + 0xA0, 12);
		memcpy(title + 12, rom + 0xAC, 4);
	}
	return title;
}

EMSCRIPTEN_KEEPALIVE
const char* vj_game_code(void) {
	static char code[5];
	memset(code, 0, sizeof(code));
	if (rom && romLen >= 0xB0) {
		memcpy(code, rom + 0xAC, 4);
	}
	return code;
}

EMSCRIPTEN_KEEPALIVE
int vj_rom_revision(void) {
	/* Header byte 0xBC is the software version. Zero Mission (USA) is 0. */
	return (rom && romLen > 0xBC) ? rom[0xBC] : -1;
}

EMSCRIPTEN_KEEPALIVE
int vj_rom_size(void) { return (int) romLen; }
