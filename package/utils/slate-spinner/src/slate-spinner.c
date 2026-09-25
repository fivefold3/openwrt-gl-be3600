// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * slate-spinner - a "working" spinner animation for the GL.iNet Slate 7
 * (GL-BE3600) side screen.
 *
 * Renders into a 284x76 landscape canvas: a star cycling · ✢ * ✶ ✻ ✽ and back
 * at 120 ms per frame in orange, and a verb plus "..." in the plain
 * text colour. A new verb arrives after 2 s, then 3 s, then every 5 s, and
 * each one is revealed with the console-style transition: every 40 ms a ▌
 * head advances one character, the two characters behind it flicker
 * between ".", "_" and the real character, and the third settles.
 *
 * Touch demo: the CST816S reports fb0 coordinates on its evdev node; while
 * a finger is down the program spews random printable characters from
 * under it that drift and fade out, so the package demonstrates the touch
 * as well as the panel.
 *
 * The canvas is written to /dev/fb0 (RGB565, fb_st7789p3 built into the
 * kernel): straight in for the native 284x76 landscape fb, or rotated
 * (-r 90/270) into a portrait 76x284 one.
 *
 *   slate-spinner [-f /dev/fb0] [-r 0|90|180|270] [-b brightness]
 *                 [-i /dev/input/eventN] [-T]   touch node / no touch trail
 *                 [-t prefix]   render a few frames to prefix-N.ppm and exit
 *                 [-w]          print the pixel width of every phrase
 */
#define _GNU_SOURCE
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <glob.h>
#include <linux/fb.h>
#include <linux/input.h>
#include <math.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

#include "font_data.h"

#define CANVAS_W	284
#define CANVAS_H	76

#define FRAME_MS	33	/* ~30 fps render loop; fbtft pushes at 40 */
#define STAR_FRAME_MS	120	/* star frame period */
#define REVEAL_STEP_MS	40	/* one reveal step */
#define REVEAL_HEAD	3	/* characters behind the ▌ still in flux */

/* verb schedule: [2000, 3000, 5000] then 5000 forever */
static const int phrase_delays[] = { 2000, 3000, 5000 };
#define NPHRASE_DELAYS	((int)(sizeof(phrase_delays) / sizeof(phrase_delays[0])))

/* layout */
#define STAR_CX		22
#define STAR_CY		38
#define TEXT_X		44
#define TEXT_BASE	44

#define MAX_CHARS	64

/* touch trail */
#define MAX_PARTICLES	96
#define TRAIL_PER_REPORT 1	/* characters spawned per touch report */
#define TRAIL_ON_DOWN	6	/* extra burst when the finger lands */
#define TRAIL_LIFE_MIN	500	/* ms */
#define TRAIL_LIFE_MAX	900
#define TRAIL_SPEED_MIN	20.0f	/* px/s */
#define TRAIL_SPEED_MAX	70.0f
#define TOUCH_RETRY_MS	2000	/* re-scan for the touch node if missing */

static const char *phrases[] = {
	"Counting packets",
	"Routing traffic",
	"Translating addresses",
	"Prioritizing bits",
	"Detecting protocols",
	"Conducting handshakes",
	"Emitting Wi-Fi",
	"Optimizing channels",
	"Resolving domain names",
	"Forwarding ports",
	"Shuffling subnets",
	"Herding datagrams",
	"Negotiating MTUs",
	"Beamforming",
	"Spinning up radios",
	"Warming the antennas",
	"Aligning beacons",
	"Leasing addresses",
	"Hopping channels",
	"Encrypting streams",
	"Inspecting headers",
	"Reassembling fragments",
	"Tunneling traffic",
	"Balancing loads",
	"Shaping traffic",
	"Queueing frames",
	"Acknowledging ACKs",
	"Retransmitting",
	"Tracing routes",
	"Bridging VLANs",
	"Multiplexing",
	"Modulating carriers",
	"Amplifying signals",
	"Calibrating radios",
	"Scanning the spectrum",
	"Dodging microwaves",
	"Summoning DHCP",
	"Consulting the DNS",
	"Caching answers",
	"Flushing caches",
	"Learning MAC addresses",
	"Persuading NAT",
	"Punching holes",
	"Keeping alive",
	"Syncing clocks",
	"Counting hops",
	"Hashing flows",
	"Marking packets",
	"Sniffing the air",
	"Whispering to the WAN",
	"Listening on the LAN",
	"Spinning the fan",
	"Twiddling bits",
	"Reticulating splines",
	"Cogitating",
	"Pondering packets",
	"Untangling cables",
	"Polishing photons",
	"Decrypting payloads",
	"Checking checksums",
	"Fragmenting frames",
	"Aging out ARP entries",
	"Massaging metadata",
	"Debouncing buttons",
	"Herding electrons",
	"Quantizing QoS",
	"Juggling jumbo frames",
	"Deduplicating DNS",
	"Greasing the pipes",
	"Inflating the tubes",
	"Aggregating links",
	"Deferring to the Q6",
	"Consulting the ART",
	"Bonding channels",
	"Steering bands",
	"Chasing the WAN link",
	"Adjusting the antennae",
	"Coalescing interrupts",
	"Wrangling wireguard",
	"Feeding the firewall",
};
#define NPHRASES	((int)(sizeof(phrases) / sizeof(phrases[0])))

/* [...frames, ...frames.reverse()]: 12 frames, the ends doubled */
static const uint32_t star_cps[] = { 0xB7, 0x2722, '*', 0x2736, 0x273B, 0x273D };
#define NSTAR		((int)(sizeof(star_cps) / sizeof(star_cps[0])))
#define NSTAR_SEQ	(2 * NSTAR)

struct rgb {
	uint8_t r, g, b;
};

/* star colour (#d97757) and the text colour */
static const struct rgb star_orange = { 217, 119, 87 };
static const struct rgb text_white = { 255, 255, 255 };

/* the trail picks from these */
static const struct rgb trail_palette[] = {
	{ 255, 255, 255 }, { 217, 119, 87 }, { 245, 149, 117 }, { 170, 170, 170 },
};
#define NTRAIL_PALETTE	((int)(sizeof(trail_palette) / sizeof(trail_palette[0])))

static uint8_t canvas[CANVAS_H][CANVAS_W][3];

static volatile sig_atomic_t stop;

static void on_signal(int sig)
{
	(void)sig;
	stop = 1;
}

static int64_t now_ms(void)
{
	struct timespec ts;

	clock_gettime(CLOCK_MONOTONIC, &ts);
	return (int64_t)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

/* --- text ------------------------------------------------------------- */

static uint32_t utf8_next(const char **s)
{
	const unsigned char *p = (const unsigned char *)*s;
	uint32_t cp;
	int n;

	if (p[0] < 0x80) {
		cp = p[0];
		n = 1;
	} else if ((p[0] & 0xE0) == 0xC0) {
		cp = p[0] & 0x1F;
		n = 2;
	} else if ((p[0] & 0xF0) == 0xE0) {
		cp = p[0] & 0x0F;
		n = 3;
	} else {
		cp = p[0] & 0x07;
		n = 4;
	}
	for (int i = 1; i < n; i++) {
		if ((p[i] & 0xC0) != 0x80) {
			n = i;
			break;
		}
		cp = (cp << 6) | (p[i] & 0x3F);
	}
	*s += n;
	return cp;
}

/* decode into code points, returns the count */
static int utf8_decode(const char *s, uint32_t *out, int max)
{
	int n = 0;

	while (*s && n < max)
		out[n++] = utf8_next(&s);
	return n;
}

static const struct sg_glyph *find_glyph(const struct sg_face *f, uint32_t cp)
{
	for (int i = 0; i < f->nglyphs; i++)
		if (f->glyphs[i].cp == cp)
			return &f->glyphs[i];
	if (cp != '?')
		return find_glyph(f, '?');
	return NULL;
}

static void blend(int x, int y, const struct rgb *c, float a)
{
	uint8_t *px;

	if (x < 0 || x >= CANVAS_W || y < 0 || y >= CANVAS_H || a <= 0.0f)
		return;
	if (a > 1.0f)
		a = 1.0f;
	px = canvas[y][x];
	px[0] = px[0] + (c->r - px[0]) * a;
	px[1] = px[1] + (c->g - px[1]) * a;
	px[2] = px[2] + (c->b - px[2]) * a;
}

static void draw_glyph(const struct sg_face *f, const struct sg_glyph *g,
		       int pen_x, int base_y, const struct rgb *col, float alpha)
{
	const uint8_t *bm = f->pixels + g->off;
	int ox = pen_x + g->bx, oy = base_y + g->by;

	for (int j = 0; j < g->h; j++)
		for (int i = 0; i < g->w; i++) {
			uint8_t a = bm[j * g->w + i];

			if (a)
				blend(ox + i, oy + j, col, (a / 255.0f) * alpha);
		}
}

/* draw n code points; col NULL only measures. Returns the pen after them */
static int draw_cps(const struct sg_face *f, int pen_x, int base_y,
		    const uint32_t *cps, int n, const struct rgb *col)
{
	int x16 = pen_x * 16;

	for (int i = 0; i < n; i++) {
		const struct sg_glyph *g = find_glyph(f, cps[i]);

		if (!g)
			continue;
		if (col)
			draw_glyph(f, g, x16 >> 4, base_y, col, 1.0f);
		x16 += g->adv16;
	}
	return (x16 + 15) >> 4;
}

static int text_width(const struct sg_face *f, const char *s)
{
	uint32_t cps[MAX_CHARS];
	int n = utf8_decode(s, cps, MAX_CHARS);

	return draw_cps(f, 0, 0, cps, n, NULL);
}

/* the star glyphs differ in size: centre each one's box on (cx, cy) */
static void draw_star(int frame, int cx, int cy, const struct rgb *col)
{
	const struct sg_glyph *g = find_glyph(&sg_star, star_cps[frame]);

	if (!g)
		return;
	draw_glyph(&sg_star, g, cx - (g->bx + g->w / 2), cy - (g->by + g->h / 2),
		   col, 1.0f);
}

/* --- animation state -------------------------------------------------- */

struct anim {
	int64_t t0;		/* start of the animation */
	int bag[NPHRASES];	/* shuffled phrase order, no repeats per round */
	int bag_pos;
	int changes;		/* phrases shown so far, for the delay schedule */
	int64_t next_change;	/* when the next verb arrives */
	int width;		/* padded length: longest phrase + "..." */
	uint32_t target[MAX_CHARS];	/* the verb + "...", space padded */
	uint32_t shown[MAX_CHARS];	/* what is on screen right now */
	int64_t reveal_since;	/* when the current reveal started */
	int reveal_step;	/* steps of the reveal already applied */
};

static void shuffle_bag(struct anim *a)
{
	int last = a->bag_pos > 0 ? a->bag[NPHRASES - 1] : -1;

	for (int i = 0; i < NPHRASES; i++)
		a->bag[i] = i;
	for (int i = NPHRASES - 1; i > 0; i--) {
		int j = rand() % (i + 1), t = a->bag[i];

		a->bag[i] = a->bag[j];
		a->bag[j] = t;
	}
	/* never show the same phrase twice in a row across rounds */
	if (a->bag[0] == last && NPHRASES > 1) {
		int t = a->bag[0];

		a->bag[0] = a->bag[1];
		a->bag[1] = t;
	}
	a->bag_pos = 0;
}

static void next_phrase(struct anim *a, int64_t now)
{
	char buf[MAX_CHARS];
	int n, delay;

	if (a->bag_pos >= NPHRASES)
		shuffle_bag(a);
	snprintf(buf, sizeof(buf), "%s...", phrases[a->bag[a->bag_pos++]]);
	n = utf8_decode(buf, a->target, a->width);
	for (int i = n; i < a->width; i++)
		a->target[i] = ' ';
	a->reveal_since = now;
	a->reveal_step = 0;

	delay = phrase_delays[a->changes < NPHRASE_DELAYS ? a->changes
							  : NPHRASE_DELAYS - 1];
	a->changes++;
	a->next_change = now + delay;
}

/*
 * One 40 ms step of the reveal: the character at the head becomes
 * ▌, the two behind it a random pick of ".", "_" or the real character, the
 * third the real character (spaces stay spaces).
 */
static uint32_t reveal_char(uint32_t target, int behind)
{
	static const uint32_t flux[] = { '.', '_' };

	if (target == ' ')
		return ' ';
	switch (behind) {
	case 0:
		return 0x258C;	/* ▌ */
	case 1:
	case 2: {
		int r = rand() % 3;

		return r < 2 ? flux[r] : target;
	}
	default:
		return target;
	}
}

static void reveal_step(struct anim *a)
{
	int v = a->reveal_step;

	if (v - REVEAL_HEAD >= a->width)
		return;		/* done: everything has settled */
	a->reveal_step++;
	for (int b = 0; b <= REVEAL_HEAD; b++) {
		int k = v - b;

		if (k >= 0 && k < a->width)
			a->shown[k] = reveal_char(a->target[k], b);
	}
}

static void compose(struct anim *a, int64_t now)
{
	int64_t t = now - a->t0;
	int frame, want;

	memset(canvas, 0, sizeof(canvas));

	if (now >= a->next_change)
		next_phrase(a, now);

	/* catch the reveal up to the current time, one step per 40 ms */
	want = (int)((now - a->reveal_since) / REVEAL_STEP_MS) + 1;
	while (a->reveal_step < want && a->reveal_step - REVEAL_HEAD < a->width)
		reveal_step(a);

	frame = (int)((t / STAR_FRAME_MS) % NSTAR_SEQ);
	if (frame >= NSTAR)
		frame = NSTAR_SEQ - 1 - frame;
	draw_star(frame, STAR_CX, STAR_CY, &star_orange);

	draw_cps(&sg_main, TEXT_X, TEXT_BASE, a->shown, a->width, &text_white);
}

static void anim_init(struct anim *a, int64_t now)
{
	memset(a, 0, sizeof(*a));
	a->t0 = now;
	for (int i = 0; i < NPHRASES; i++) {
		char buf[MAX_CHARS];
		uint32_t cps[MAX_CHARS];
		int n;

		snprintf(buf, sizeof(buf), "%s...", phrases[i]);
		n = utf8_decode(buf, cps, MAX_CHARS);
		if (n > a->width)
			a->width = n;
	}
	for (int i = 0; i < a->width; i++)
		a->shown[i] = ' ';
	shuffle_bag(a);
	next_phrase(a, now);
}

/* --- touch trail -------------------------------------------------------- */

struct particle {
	float x, y, vx, vy;
	int64_t born;
	int life;		/* ms */
	uint32_t cp;
	const struct rgb *col;
};

struct trail {
	struct particle p[MAX_PARTICLES];
	int n;
	int fd;			/* touch evdev node, -1 when absent */
	char path[64];		/* "" = find it by name */
	int64_t next_scan;
	int x, y;		/* last reported finger position */
	bool touching;
};

static float frand(float lo, float hi)
{
	return lo + (hi - lo) * ((float)rand() / (float)RAND_MAX);
}

static void trail_spawn(struct trail *t, int x, int y, int count, int64_t now)
{
	for (int i = 0; i < count; i++) {
		struct particle *p;
		float ang = frand(0.0f, 6.2831853f);
		float speed = frand(TRAIL_SPEED_MIN, TRAIL_SPEED_MAX);

		if (t->n < MAX_PARTICLES) {
			p = &t->p[t->n++];
		} else {
			/* full: recycle the oldest */
			p = &t->p[0];
			for (int j = 1; j < MAX_PARTICLES; j++)
				if (t->p[j].born < p->born)
					p = &t->p[j];
		}
		p->x = x + frand(-4.0f, 4.0f);
		p->y = y + frand(-4.0f, 4.0f);
		p->vx = speed * cosf(ang);
		p->vy = speed * sinf(ang) - 15.0f;	/* a slight upward drift */
		p->born = now;
		p->life = TRAIL_LIFE_MIN + rand() % (TRAIL_LIFE_MAX - TRAIL_LIFE_MIN);
		p->cp = '!' + rand() % ('~' - '!' + 1);
		p->col = &trail_palette[rand() % NTRAIL_PALETTE];
	}
}

static void trail_draw(struct trail *t, int64_t now)
{
	int i = 0;

	while (i < t->n) {
		struct particle *p = &t->p[i];
		int64_t age = now - p->born;
		const struct sg_glyph *g;
		float a;

		if (age >= p->life) {
			t->p[i] = t->p[--t->n];
			continue;
		}
		a = 1.0f - (float)age / p->life;
		a *= a;	/* linger, then drop away */
		g = find_glyph(&sg_main, p->cp);
		if (g)
			draw_glyph(&sg_main, g,
				   (int)(p->x + p->vx * age / 1000.0f) - g->w / 2,
				   (int)(p->y + p->vy * age / 1000.0f) + g->h / 2,
				   p->col, a);
		i++;
	}
}

/* the CST816x node: by name, so an extra input device does not confuse it */
static int touch_find(char *path, size_t len)
{
	DIR *d = opendir("/sys/class/input");
	struct dirent *e;
	int found = 0;

	if (!d)
		return 0;
	while (!found && (e = readdir(d))) {
		char np[256], name[128] = "";
		FILE *f;

		if (strncmp(e->d_name, "event", 5))
			continue;
		snprintf(np, sizeof(np), "/sys/class/input/%.32s/device/name", e->d_name);
		f = fopen(np, "r");
		if (!f)
			continue;
		if (fgets(name, sizeof(name), f) &&
		    (strstr(name, "CST816") || strstr(name, "cst816") ||
		     strstr(name, "Touchscreen"))) {
			snprintf(path, len, "/dev/input/%.32s", e->d_name);
			found = 1;
		}
		fclose(f);
	}
	closedir(d);
	return found;
}

static void touch_open(struct trail *t, int64_t now)
{
	char path[64];

	t->next_scan = now + TOUCH_RETRY_MS;
	if (t->path[0])
		snprintf(path, sizeof(path), "%s", t->path);
	else if (!touch_find(path, sizeof(path)))
		return;
	t->fd = open(path, O_RDONLY | O_NONBLOCK);
	if (t->fd >= 0)
		fprintf(stderr, "touch trail on %s\n", path);
}

/* drain the pending events; spawn on every report with the finger down */
static void touch_poll(struct trail *t, int64_t now)
{
	struct input_event ev[32];
	ssize_t r;

	if (t->fd < 0) {
		if (now >= t->next_scan)
			touch_open(t, now);
		return;
	}
	while ((r = read(t->fd, ev, sizeof(ev))) > 0) {
		for (int i = 0; i < (int)(r / sizeof(ev[0])); i++) {
			switch (ev[i].type) {
			case EV_ABS:
				if (ev[i].code == ABS_X)
					t->x = ev[i].value;
				else if (ev[i].code == ABS_Y)
					t->y = ev[i].value;
				break;
			case EV_KEY:
				if (ev[i].code == BTN_TOUCH) {
					if (ev[i].value && !t->touching)
						trail_spawn(t, t->x, t->y, TRAIL_ON_DOWN, now);
					t->touching = ev[i].value != 0;
				}
				break;
			case EV_SYN:
				if (ev[i].code == SYN_REPORT && t->touching)
					trail_spawn(t, t->x, t->y, TRAIL_PER_REPORT, now);
				break;
			}
		}
	}
	if (r < 0 && errno != EAGAIN) {
		/* the node went away (driver unbound): look for it again */
		fprintf(stderr, "touch: %s\n", strerror(errno));
		close(t->fd);
		t->fd = -1;
		t->touching = false;
	}
}

/* --- output ----------------------------------------------------------- */

struct fbdev {
	int fd;
	uint8_t *mem;
	size_t size;
	struct fb_var_screeninfo var;
	struct fb_fix_screeninfo fix;
	int rotation;
	uint8_t *stage;
};

static int fb_open(struct fbdev *fb, const char *path, int rotation)
{
	int want_w = CANVAS_W, want_h = CANVAS_H;

	fb->fd = open(path, O_RDWR);
	if (fb->fd < 0)
		return -errno;
	if (ioctl(fb->fd, FBIOGET_VSCREENINFO, &fb->var) ||
	    ioctl(fb->fd, FBIOGET_FSCREENINFO, &fb->fix)) {
		close(fb->fd);
		return -EIO;
	}
	if (rotation == 90 || rotation == 270) {
		want_w = CANVAS_H;
		want_h = CANVAS_W;
	}
	if (fb->var.bits_per_pixel != 16 || fb->var.xres != (unsigned)want_w ||
	    fb->var.yres != (unsigned)want_h) {
		fprintf(stderr, "%s is %ux%u @ %u bpp, need %dx%d @ 16 for rotation %d\n",
			path, fb->var.xres, fb->var.yres, fb->var.bits_per_pixel,
			want_w, want_h, rotation);
		close(fb->fd);
		return -EINVAL;
	}
	fb->size = (size_t)fb->fix.line_length * fb->var.yres;
	fb->mem = mmap(NULL, fb->size, PROT_READ | PROT_WRITE, MAP_SHARED, fb->fd, 0);
	if (fb->mem == MAP_FAILED) {
		close(fb->fd);
		return -errno;
	}
	fb->stage = calloc(1, fb->size);
	fb->rotation = rotation;
	return 0;
}

static inline uint16_t rgb565(const uint8_t *p)
{
	return ((p[0] & 0xF8) << 8) | ((p[1] & 0xFC) << 3) | (p[2] >> 3);
}

/* rotate the landscape canvas into the panel's native orientation */
static void fb_blit(struct fbdev *fb)
{
	for (int y = 0; y < CANVAS_H; y++) {
		for (int x = 0; x < CANVAS_W; x++) {
			int fx, fy;

			switch (fb->rotation) {
			case 90:
				fx = CANVAS_H - 1 - y;
				fy = x;
				break;
			case 180:
				fx = CANVAS_W - 1 - x;
				fy = CANVAS_H - 1 - y;
				break;
			case 270:
				fx = y;
				fy = CANVAS_W - 1 - x;
				break;
			default:
				fx = x;
				fy = y;
				break;
			}
			*(uint16_t *)(fb->stage + fy * fb->fix.line_length + fx * 2) =
				rgb565(canvas[y][x]);
		}
	}
	memcpy(fb->mem, fb->stage, fb->size);
}

static void fb_clear(struct fbdev *fb)
{
	memset(fb->mem, 0, fb->size);
}

static void set_brightness(int level)
{
	glob_t g;

	if (level < 0)
		return;
	if (glob("/sys/class/backlight/*/brightness", 0, NULL, &g) == 0) {
		for (size_t i = 0; i < g.gl_pathc; i++) {
			FILE *f = fopen(g.gl_pathv[i], "w");

			if (f) {
				fprintf(f, "%d\n", level);
				fclose(f);
			}
		}
	}
	globfree(&g);
}

static int write_ppm(const char *path)
{
	FILE *f = fopen(path, "wb");

	if (!f)
		return -errno;
	fprintf(f, "P6\n%d %d\n255\n", CANVAS_W, CANVAS_H);
	fwrite(canvas, 1, sizeof(canvas), f);
	fclose(f);
	return 0;
}

static void usage(void)
{
	fprintf(stderr,
		"usage: slate-spinner [-f fbdev] [-r 0|90|180|270] [-b brightness]\n"
		"                     [-t prefix] [-w]\n"
		"  -f  framebuffer device (default /dev/fb0)\n"
		"  -r  canvas rotation into the framebuffer (default 0)\n"
		"  -b  backlight brightness to set once the panel is up\n"
		"  -i  touch input node (default: the CST816x node, found by name)\n"
		"  -T  no touch trail\n"
		"  -t  test: write frames to prefix-N.ppm instead of the panel\n"
		"  -w  print the pixel width of every phrase and exit\n");
}

int main(int argc, char **argv)
{
	const char *fbpath = "/dev/fb0", *test_prefix = NULL;
	int rotation = 0, brightness = -1, opt;
	bool touch = true;
	struct fbdev fb = { .fd = -1 };
	struct trail trail = { .fd = -1 };
	struct anim a;
	int64_t next;

	while ((opt = getopt(argc, argv, "f:r:b:i:Tt:wh")) != -1) {
		switch (opt) {
		case 'f':
			fbpath = optarg;
			break;
		case 'r':
			rotation = atoi(optarg);
			if (rotation % 90 || rotation < 0 || rotation > 270) {
				usage();
				return 2;
			}
			break;
		case 'b':
			brightness = atoi(optarg);
			break;
		case 'i':
			snprintf(trail.path, sizeof(trail.path), "%s", optarg);
			break;
		case 'T':
			touch = false;
			break;
		case 't':
			test_prefix = optarg;
			break;
		case 'w': {
			int widest = 0, avail = CANVAS_W - TEXT_X - 4;

			for (int i = 0; i < NPHRASES; i++) {
				char buf[MAX_CHARS];
				int w;

				snprintf(buf, sizeof(buf), "%s...", phrases[i]);
				w = text_width(&sg_main, buf);
				printf("%4d px %s%s\n", w, buf, w > avail ? "  TOO WIDE" : "");
				if (w > widest)
					widest = w;
			}
			printf("widest %d px of %d available\n", widest, avail);
			return 0;
		}
		default:
			usage();
			return 2;
		}
	}

	srand((unsigned)time(NULL) ^ (unsigned)getpid());
	anim_init(&a, now_ms());

	if (test_prefix) {
		/* frames through the first reveal and into the second phrase,
		 * with a synthetic finger dragged across the lower half */
		static const int at[] = { 0, 200, 400, 700, 1000, 2100, 2300, 2600 };

		for (int i = 0; i < (int)(sizeof(at) / sizeof(at[0])); i++) {
			char path[256];
			int64_t now = a.t0 + at[i];

			if (touch && at[i] >= 400) {
				int fx = 60 + (at[i] - 400) / 12;

				if (at[i] == 400)
					trail_spawn(&trail, fx, 60, TRAIL_ON_DOWN, now);
				for (int k = 0; k < 8; k++)
					trail_spawn(&trail, fx - k * 2, 60,
						    TRAIL_PER_REPORT, now - k * 30);
			}
			compose(&a, now);
			trail_draw(&trail, now);
			snprintf(path, sizeof(path), "%s-%d.ppm", test_prefix, i);
			if (write_ppm(path))
				perror(path);
		}
		return 0;
	}

	signal(SIGTERM, on_signal);
	signal(SIGINT, on_signal);

	/* the panel driver may still be probing at boot: wait for it */
	for (int tries = 0; tries < 120; tries++) {
		int rc = fb_open(&fb, fbpath, rotation);

		if (rc == 0)
			break;
		if (rc == -EINVAL)
			return 1;
		if (tries == 0)
			fprintf(stderr, "waiting for %s: %s\n", fbpath, strerror(-rc));
		if (stop)
			return 0;
		usleep(500000);
	}
	if (fb.fd < 0) {
		fprintf(stderr, "%s never appeared\n", fbpath);
		return 1;
	}

	set_brightness(brightness);
	if (touch)
		touch_open(&trail, now_ms());

	next = now_ms();
	while (!stop) {
		int64_t now = now_ms();

		if (touch)
			touch_poll(&trail, now);
		compose(&a, now);
		trail_draw(&trail, now);
		fb_blit(&fb);
		next += FRAME_MS;
		now = now_ms();
		if (next > now)
			usleep((next - now) * 1000);
		else
			next = now;
	}

	fb_clear(&fb);
	return 0;
}
