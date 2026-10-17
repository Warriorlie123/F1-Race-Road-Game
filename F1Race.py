# F1 Race Road
# Ömer Burhan Çınar
#
# Top-down endless racer written with pygame.
# Run:  pip install pygame   then   python F1Race.py
#
# Controls
#   A / D or LEFT / RIGHT   steer
#   W or UP                 throttle (no throttle = the car coasts and slows)
#   S or DOWN               brake
#   SHIFT                   open DRS (when the meter is full)
#   SPACE / P               pause
#   ESC                     back / quit

import os
import sys
import math
import random
import asyncio
import pygame

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# True when running in the browser (pygbag / WebAssembly build)
IS_WEB = sys.platform == "emscripten"


def asset(*parts):
    # paths are resolved from the script folder, not the working dir,
    # so the game starts no matter where it is launched from
    return os.path.join(BASE_DIR, *parts)


W, H = 700, 650
FPS = 60

# track layout (screen px)
BARRIER = 14            # tyre wall at both screen edges
ROAD_L, ROAD_R = 120, 580
KERB_W = 16
LANES = 3
LANE_W = (ROAD_R - ROAD_L) / LANES

SPRITE_SCALE = 0.6

# Scale. The car sprite is ~5.6 m long and ~140 px tall, so laterally we
# are at roughly 25 px/m. If the scroll used the same scale, 300 km/h would
# be ~35 px per frame and nobody could play it. So the longitudinal axis is
# compressed to 10 px/m. Speeds and distances on the HUD are computed in
# real units (m, m/s) and only converted to px at the drawing step.
PX_PER_M = 10.0

# longitudinal model, SI units. The player drives the throttle (W) and the
# brake (S); the car's speed comes out of a force balance:
#   m * dv/dt = F_drive - F_drag - F_roll - F_brake
#   F_drive = min(P / v, m * A_TRACTION)     power-limited, grip-limited at low speed
#   F_drag  = 0.5 * rho * CdA * v^2
# With these numbers top speed (P = drag power) is about 360 km/h, and DRS,
# which flattens the rear wing, lowers CdA and raises it to about 385 km/h.
CAR_MASS = 798.0        # kg, F1 minimum weight with driver
ENGINE_POWER = 740e3    # W, ~1000 hp
A_TRACTION = 11.0       # m/s^2, max acceleration the rear tyres can put down
RHO = 1.2               # kg/m^3, air density
CDA = 1.25              # m^2, drag coefficient times frontal area
CDA_DRS = 1.02          # m^2, with the DRS flap open
ROLL_COEF = 0.015       # rolling resistance coefficient
ENGINE_BRAKE = 2200.0   # N, drag from the engine when you lift off the throttle
BRAKE_DECEL = 28.0      # m/s^2, ~2.9 g of braking
GRASS_DRAG = 0.25       # extra resistance on grass, as a fraction of weight
GRASS_GRIP = 0.30       # on grass the tyres can only put down 30% of the traction
G = 9.81
TAU_PROGRESS = 75.0     # s, how fast the traffic gets denser over a run
DRS_TIME = 4.0
DRS_PER_PASS = 0.125    # 8 clean overtakes fill the meter

# lateral model: m*x'' = F_steer - c*x'
# with per-unit-mass values this is a first order system in velocity:
#   terminal speed = STEER_ACC / LAT_DAMP   (~600 px/s)
#   time constant  = 1 / LAT_DAMP           (~0.14 s)
STEER_ACC = 4200.0
LAT_DAMP = 7.0

# backmarkers run at a fraction of our speed, so they close in at
# (1 - TRAFFIC_RATIO) * v
TRAFFIC_RATIO = 0.4

# drivetrain, only used for the gear/rpm readout but the numbers are real-ish
WHEEL_R = 0.33          # m
FINAL_DRIVE = 3.0
GEARS = [4.90, 4.11, 3.45, 2.89, 2.43, 2.04, 1.71, 1.44]   # geometric, step ~0.84
RPM_IDLE = 4000
RPM_SHIFT = 11800
RPM_LIMIT = 15000

NEAR_MISS_PX = 22
NEAR_MISS_PTS = 50

# colours
WHITE = (240, 240, 240)
BLACK = (10, 10, 12)
GREY = (120, 128, 140)
DARK = (22, 24, 28)
RED = (225, 30, 35)
GREEN = (40, 210, 90)
BLUE = (40, 120, 255)
YELLOW = (255, 205, 40)
GRASS = (46, 110, 52)
KERB_RED = (200, 25, 30)


def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def kmh_to_ms(v):
    return v / 3.6


def engine_state(v_ms):
    # pick the lowest gear that keeps the engine under the shift point.
    # stateless, so there's no hysteresis; fine for a readout, a real
    # gearbox controller would need a down-shift band to avoid hunting.
    if v_ms < 0.5:
        return 1, RPM_IDLE
    wheel_rpm = v_ms / WHEEL_R * 60.0 / (2 * math.pi)
    for i, g in enumerate(GEARS):
        rpm = wheel_rpm * g * FINAL_DRIVE
        if rpm <= RPM_SHIFT or i == len(GEARS) - 1:
            # below idle the clutch would be slipping, so clamp it
            return i + 1, clamp(rpm, RPM_IDLE, RPM_LIMIT)


# ---------------------------------------------------------------- init

pygame.init()
try:
    pygame.mixer.init()
    AUDIO_OK = True
except pygame.error:
    AUDIO_OK = False        # no sound card, game still runs

screen = pygame.display.set_mode((W, H))
pygame.display.set_caption("F1 Race Road")
clock = pygame.time.Clock()


class _Mute:
    def play(self, *a, **k):
        pass

    def stop(self):
        pass

    def set_volume(self, v):
        pass


def audio_file(name):
    # browsers play Ogg Vorbis reliably, so the web build uses .ogg copies
    if IS_WEB:
        name = os.path.splitext(name)[0] + ".ogg"
    return asset("audio", name)


def load_sound(name, vol=1.0):
    if not AUDIO_OK:
        return _Mute()
    try:
        s = pygame.mixer.Sound(audio_file(name))
        s.set_volume(vol)
        return s
    except (pygame.error, FileNotFoundError):
        return _Mute()


def load_img(name, scale=1.0):
    img = pygame.image.load(asset("images", name)).convert_alpha()
    if scale != 1.0:
        w, h = img.get_size()
        img = pygame.transform.smoothscale(img, (int(w * scale), int(h * scale)))
    return img


def seamless_v(surf):
    # stacking an image on top of its own vertical mirror gives a strip
    # that tiles with no visible seam. cheap trick, works for noise textures
    w, h = surf.get_size()
    out = pygame.Surface((w, h * 2), pygame.SRCALPHA)
    out.blit(surf, (0, 0))
    out.blit(pygame.transform.flip(surf, False, True), (0, h))
    return out


try:
    pygame.display.set_icon(pygame.image.load(asset("images", "logo.png")))
except (pygame.error, FileNotFoundError):
    pass

IMG_CAR = load_img("car.png", SPRITE_SCALE)
IMG_CAR_L = load_img("car_left.png", SPRITE_SCALE)
IMG_CAR_R = load_img("car_right.png", SPRITE_SCALE)
IMG_OBST = load_img("obstacle.png", SPRITE_SCALE)

MASK_CAR = pygame.mask.from_surface(IMG_CAR)
MASK_CAR_L = pygame.mask.from_surface(IMG_CAR_L)
MASK_CAR_R = pygame.mask.from_surface(IMG_CAR_R)
MASK_OBST = pygame.mask.from_surface(IMG_OBST)

# tarmac: cut a clean piece out of the old background (between the edge
# line and the centre dashes) and scale it to the road width
_bg = load_img("background.png")
_crop = _bg.subsurface((80, 20, 320, 820)).copy()
_k = (ROAD_R - ROAD_L) / 320
_crop = pygame.transform.smoothscale(_crop, (ROAD_R - ROAD_L, int(820 * _k)))
TARMAC = seamless_v(_crop).convert()

# grass: flat green with the speckle texture on top
_speck = load_img("texture.png")
_speck.set_alpha(110)
_g = pygame.Surface(_speck.get_size())
_g.fill(GRASS)
_g.blit(_speck, (0, 0))
GRASS_TILE = seamless_v(_g).convert()

SND_INTRO1 = load_sound("intro1.wav", 0.7)
SND_INTRO2 = load_sound("intro2.wav", 0.7)
SND_CRASH = load_sound("car_crash.wav", 0.9)
SND_IGNITION = load_sound("ignition.wav", 0.8)

if AUDIO_OK:
    try:
        pygame.mixer.music.load(audio_file("running.wav"))
        MUSIC_OK = True
    except pygame.error:
        MUSIC_OK = False
else:
    MUSIC_OK = False


def music_play():
    if MUSIC_OK:
        pygame.mixer.music.play(-1)


def music_stop():
    if MUSIC_OK:
        pygame.mixer.music.stop()


def music_volume(v):
    if MUSIC_OK:
        pygame.mixer.music.set_volume(clamp(v, 0, 1))


def music_pause(p):
    if MUSIC_OK:
        (pygame.mixer.music.pause if p else pygame.mixer.music.unpause)()


# fonts are created once. the old version built a new Font object every
# frame which was a big chunk of the frame time
F_HUGE = pygame.font.Font(None, 84)
F_BIG = pygame.font.Font(None, 64)
F_MED = pygame.font.Font(None, 36)
F_SMALL = pygame.font.Font(None, 24)
F_TINY = pygame.font.Font(None, 19)
F_MONO = pygame.font.SysFont("consolas,menlo,dejavusansmono,couriernew", 30, bold=True)
F_MONO_S = pygame.font.SysFont("consolas,menlo,dejavusansmono,couriernew", 16, bold=True)


def text(surf, s, font, color, center=None, topleft=None, topright=None, shadow=True):
    img = font.render(s, True, color)
    r = img.get_rect()
    if center:
        r.center = center
    elif topright:
        r.topright = topright
    else:
        r.topleft = topleft or (0, 0)
    if shadow:
        surf.blit(font.render(s, True, BLACK), r.move(2, 2))
    surf.blit(img, r)
    return r


# ---------------------------------------------------------------- high score

SCORE_FILE = asset("textfile", "high_score.txt")


def read_best():
    try:
        with open(SCORE_FILE) as f:
            return int(f.read().strip() or 0)
    except (OSError, ValueError):
        return 0      # missing or corrupted file -> start from zero


def save_best(v):
    # write to a temp file then swap it in. if the game is killed mid-write
    # the old score survives instead of ending up as an empty file
    os.makedirs(os.path.dirname(SCORE_FILE), exist_ok=True)
    tmp = SCORE_FILE + ".tmp"
    with open(tmp, "w") as f:
        f.write(str(int(v)))
    os.replace(tmp, SCORE_FILE)


# ---------------------------------------------------------------- track drawing

class Track:
    def __init__(self):
        self.offset = 0.0     # px travelled, drives every scrolling layer

    def advance(self, px):
        self.offset += px

    def _tile(self, surf, tile, x, x_end, speed_mult=1.0):
        th = tile.get_height()
        tw = tile.get_width()
        y = (self.offset * speed_mult) % th - th
        surf.set_clip(pygame.Rect(x, 0, x_end - x, H))
        while y < H:
            xx = x
            while xx < x_end:
                surf.blit(tile, (xx, y))
                xx += tw
            y += th
        surf.set_clip(None)

    def draw(self, surf):
        off = self.offset

        # grass run-off on both sides
        self._tile(surf, GRASS_TILE, BARRIER, ROAD_L - KERB_W)
        self._tile(surf, GRASS_TILE, ROAD_R + KERB_W, W - BARRIER)

        # tarmac
        self._tile(surf, TARMAC, ROAD_L, ROAD_R)

        # kerbs, 30 px red / 30 px white
        blk = 30
        y0 = off % (blk * 2) - blk * 2
        i = 0
        y = y0
        while y < H:
            c = KERB_RED if i % 2 == 0 else WHITE
            pygame.draw.rect(surf, c, (ROAD_L - KERB_W, y, KERB_W, blk))
            pygame.draw.rect(surf, c, (ROAD_R, y, KERB_W, blk))
            y += blk
            i += 1

        # white edge lines
        pygame.draw.rect(surf, WHITE, (ROAD_L, 0, 4, H))
        pygame.draw.rect(surf, WHITE, (ROAD_R - 4, 0, 4, H))

        # lane dashes. period has to stay well above the per-frame scroll
        # (~16 px at top speed) or the dashes start to strobe backwards
        dash, period = 48, 120
        y = off % period - period
        while y < H:
            for k in range(1, LANES):
                x = int(ROAD_L + LANE_W * k) - 3
                pygame.draw.rect(surf, (225, 225, 220), (x, y, 6, dash))
            y += period

        # tyre wall
        seg = 22
        y = off % (seg * 2) - seg * 2
        i = 0
        while y < H:
            c = (30, 60, 150) if i % 2 == 0 else (230, 230, 230)
            pygame.draw.rect(surf, c, (0, y, BARRIER, seg))
            pygame.draw.rect(surf, c, (W - BARRIER, y, BARRIER, seg))
            y += seg
            i += 1


# ---------------------------------------------------------------- traffic

def lane_center(i):
    return ROAD_L + LANE_W * (i + 0.5)


# Where a backmarker can sit: the 3 lane centres plus the 2 lane lines.
# With centres only, driving on a dashed line was almost a free pass,
# since nothing ever spawned there.
SLOTS = [ROAD_L + LANE_W * k / 2 for k in range(1, 2 * LANES)]


class Backmarker:
    def __init__(self, x, y):
        self.rect = IMG_OBST.get_rect(center=(int(x), 0))
        self.rect.bottom = int(y)
        self.y = float(self.rect.y)
        self.passed = False
        self.min_clear = 999

    def update(self, dy):
        self.y += dy
        self.rect.y = int(self.y)

    def draw(self, surf):
        surf.blit(IMG_OBST, self.rect)


def free_gaps(xs):
    # road minus the space each car takes, as a list of (left, right)
    half = IMG_OBST.get_width() / 2
    gaps = []
    left = ROAD_L
    for x in sorted(xs):
        if x - half > left:
            gaps.append((left, x - half))
        left = max(left, x + half)
    if left < ROAD_R:
        gaps.append((left, ROAD_R))
    return gaps


def intersect(a, b):
    out = []
    for a0, a1 in a:
        for b0, b1 in b:
            lo, hi = max(a0, b0), min(a1, b1)
            if hi >= lo:
                out.append((lo, hi))
    return out


def merge(ivs):
    out = []
    for a, b in sorted(ivs):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


class Spawner:
    # Fairness check. We track "reach": the set of x intervals (car centre)
    # the player could be in when a row arrives, given how fast the car can
    # move sideways. A new row is only accepted if EVERY interval of reach
    # still has a way through one of its gaps. Checking only "some interval"
    # is not enough: the player could pick a gap that turns out to be a dead
    # end, and only find out when the next row spawns, too late to react.
    # With the every-interval rule no choice is ever a trap, so a crash is
    # the driver's mistake and never the RNG's.
    #
    # Between two rows the car can only move sideways once the previous row
    # has fully passed and before the next one reaches it:
    #   t_free = (spacing - L_obst - L_car) / v_closing
    # Starting from rest, the lateral model is a first order system, so the
    # distance covered is the integral of its step response:
    #   d(t) = v_max * (t - tau * (1 - exp(-t / tau)))
    # Using plain v_max * t here would be optimistic for short windows,
    # e.g. at t = 40 ms it says 24 px while the car really moves ~3 px.
    # K_LAT < 1 is a safety factor for imperfect human input.
    #
    # Some rows also aim at the player's current line, so there is no
    # "safe spot" to camp on for the whole run.
    MARGIN = 10
    K_LAT = 0.8

    def __init__(self):
        self.gap_left = 260.0
        self.spacing = 1e9          # first row: no constraint from before
        half_car = IMG_CAR.get_width() / 2
        self.road_c = (ROAD_L + half_car, ROAD_R - half_car)
        self.reach = [self.road_c]
        self.v_lat_max = STEER_ACC / LAT_DAMP

    def allowed(self, xs):
        # car centre positions that fit through this row's gaps
        half_car = IMG_CAR.get_width() / 2 + self.MARGIN
        out = []
        for l, r in free_gaps(xs):
            if r - l >= 2 * half_car:
                out.append((l + half_car, r - half_car))
        return out

    def grown_reach(self, closing_speed):
        t_free = (self.spacing - IMG_OBST.get_height() - IMG_CAR.get_height() - 20)
        t_free = max(0.0, t_free) / max(closing_speed, 1.0)
        tau = 1.0 / LAT_DAMP
        d = self.K_LAT * self.v_lat_max * (t_free - tau * (1 - math.exp(-t_free / tau)))
        lo_lim, hi_lim = self.road_c
        # kept as separate intervals on purpose, each one is checked alone
        return [(max(lo_lim, a - d), min(hi_lim, b + d)) for a, b in self.reach]

    def make_row(self, progress, player_x, reach):
        p_two = 0.30 + 0.35 * progress
        p_three = 0.12 * progress
        r = random.random()
        n = 3 if r < p_three else 2 if r < p_three + p_two else 1
        aim = random.random() < 0.30 + 0.30 * progress
        half = IMG_OBST.get_width() / 2

        for _ in range(40):
            xs = []
            if aim:
                xs.append(clamp(player_x + random.uniform(-12, 12), ROAD_L + half, ROAD_R - half))
            for x in random.sample(SLOTS, len(SLOTS)):
                if len(xs) >= n:
                    break
                x += random.uniform(-10, 10)
                if all(abs(x - o) >= 2 * half + 8 for o in xs):
                    xs.append(x)
            allowed = self.allowed(xs)
            if all(intersect([iv], allowed) for iv in reach):
                return xs, merge(intersect(reach, allowed))
            # rejected. drop the aimed car now and then and thin the row,
            # so a bad player position can't block every attempt
            if random.random() < 0.25:
                aim = False
                n = max(1, n - 1)
        return [], merge(reach)     # skip the row entirely, always safe

    def update(self, closing_px, closing_speed, progress, cars, player_x):
        self.gap_left -= closing_px
        self.spacing += closing_px
        if self.gap_left > 0:
            return
        reach = self.grown_reach(closing_speed)
        xs, self.reach = self.make_row(progress, player_x, reach)
        self.spacing = 0.0
        for x in xs:
            cars.append(Backmarker(x, 0))

        # time gap between rows shrinks with progress, but never less than
        # one car length plus a margin
        t_gap = 1.05 - 0.35 * progress
        min_px = IMG_OBST.get_height() + 70
        self.gap_left = max(min_px, closing_speed * t_gap) + random.uniform(0, 60)


# ---------------------------------------------------------------- particles

class Particles:
    def __init__(self):
        self.items = []

    def emit(self, x, y, n, color, spd=(80, 300), life=(0.3, 0.8), size=3, up_bias=0):
        for _ in range(n):
            a = random.uniform(0, 2 * math.pi)
            s = random.uniform(*spd)
            self.items.append([x, y, math.cos(a) * s, math.sin(a) * s - up_bias,
                               random.uniform(*life), color, size])

    def update(self, dt, scroll_px=0):
        alive = []
        for p in self.items:
            p[0] += p[2] * dt
            p[1] += p[3] * dt + scroll_px
            p[2] *= 0.96
            p[3] *= 0.96
            p[4] -= dt
            if p[4] > 0:
                alive.append(p)
        self.items = alive

    def draw(self, surf):
        for x, y, _, _, life, c, s in self.items:
            r = max(1, int(s * min(1.0, life * 2)))
            pygame.draw.circle(surf, c, (int(x), int(y)), r)


class Popups:
    def __init__(self):
        self.items = []

    def add(self, s, x, y, color):
        self.items.append([s, x, y, 1.0, color])

    def update(self, dt):
        for p in self.items:
            p[2] -= 40 * dt
            p[3] -= dt
        self.items = [p for p in self.items if p[3] > 0]

    def draw(self, surf):
        for s, x, y, life, c in self.items:
            img = F_SMALL.render(s, True, c)
            img.set_alpha(int(255 * min(1, life * 2)))
            surf.blit(img, img.get_rect(center=(x, y)))


# ---------------------------------------------------------------- UI

class Button:
    def __init__(self, label, cx, cy, w=240, h=48, color=GREEN):
        self.label = label
        self.rect = pygame.Rect(0, 0, w, h)
        self.rect.center = (cx, cy)
        self.color = color

    def draw(self, surf):
        hover = self.rect.collidepoint(pygame.mouse.get_pos())
        c = tuple(min(255, v + 35) for v in self.color) if hover else self.color
        pygame.draw.rect(surf, (0, 0, 0), self.rect.move(3, 3), border_radius=6)
        pygame.draw.rect(surf, c, self.rect, border_radius=6)
        text(surf, self.label, F_MED, WHITE, center=self.rect.center)

    def clicked(self, ev):
        # react on release, not on "is the button held". the old code read
        # mouse.get_pressed() each frame, so one click on Quit/Play Again
        # could fire several times or carry over into the next screen
        return (ev.type == pygame.MOUSEBUTTONUP and ev.button == 1
                and self.rect.collidepoint(ev.pos))


def dim(surf, alpha=150):
    s = pygame.Surface((W, H), pygame.SRCALPHA)
    s.fill((0, 0, 0, alpha))
    surf.blit(s, (0, 0))


# ---------------------------------------------------------------- game

MENU, LIGHTS, RACE, PAUSED, CRASHING, RESULTS = range(6)


class Game:
    def __init__(self):
        self.world = pygame.Surface((W, H))
        self.track = Track()
        self.fx = Particles()
        self.pops = Popups()
        self.best = read_best()
        self.state = MENU
        self.menu_t = 0.0
        self.intro2_played = False
        SND_INTRO1.play()

        self.btn_start = Button("START", W // 2, 420, color=(30, 150, 70))
        self.btn_quit = Button("QUIT", W // 2, 480, color=(170, 35, 35))
        self.btn_retry = Button("RACE AGAIN", W // 2 - 130, 590, w=230, color=(30, 150, 70))
        self.btn_menu = Button("MENU", W // 2 + 130, 590, w=230, color=(70, 80, 95))

        self.reset_run()

    def reset_run(self):
        self.car_x = (ROAD_L + ROAD_R) / 2
        self.car_y = H - 125
        self.car_vx = 0.0
        self.v = 0.0               # m/s
        self.t_race = 0.0
        self.dist = 0.0            # m
        self.score = 0.0
        self.overtakes = 0
        self.near_misses = 0
        self.top_kmh = 0.0
        self.drs = 0.0             # 0..1 meter
        self.drs_t = 0.0           # time left with the flap open
        self.cars = []
        self.spawner = Spawner()
        self.shake = 0.0
        self.on_grass = False
        self.throttle_on = False
        self.brake_on = False
        self.telemetry = []        # (t, km/h), sampled at 10 Hz
        self._tel_acc = 0.0
        self.new_best = False
        self.fx.items.clear()
        self.pops.items.clear()

        self.lights_t = 0.0
        self.lights_hold = random.uniform(0.3, 1.2)   # random like the real start

    # -------- helpers

    def car_sprite(self):
        if self.car_vx < -140:
            return IMG_CAR_L, MASK_CAR_L
        if self.car_vx > 140:
            return IMG_CAR_R, MASK_CAR_R
        return IMG_CAR, MASK_CAR

    def car_rect(self):
        img, _ = self.car_sprite()
        # sprites are anchored on their centre. the left/right images are a
        # few px bigger than the straight one and anchoring on the top-left
        # corner (old code) made the car jump sideways when it turned
        return img.get_rect(center=(int(self.car_x), int(self.car_y)))

    @staticmethod
    def longitudinal_step(v, throttle, braking, drs, grass, dt):
        # sub-steps keep the explicit integration accurate at 30-60 fps
        n = 4
        h = dt / n
        for _ in range(n):
            f = 0.0
            if throttle:
                grip = GRASS_GRIP if grass else 1.0
                f += min(ENGINE_POWER / max(v, 1.0), CAR_MASS * A_TRACTION * grip)
            elif v > 0:
                f -= ENGINE_BRAKE
            cda = CDA_DRS if drs else CDA
            f -= 0.5 * RHO * cda * v * v
            if v > 0:
                f -= ROLL_COEF * CAR_MASS * G
                if grass:
                    f -= GRASS_DRAG * CAR_MASS * G
            a = f / CAR_MASS
            if braking and v > 0:
                a -= BRAKE_DECEL
            v = max(0.0, v + a * h)
        return v

    def progress(self):
        return 1 - math.exp(-self.t_race / TAU_PROGRESS)

    # -------- states

    def start_lights(self):
        self.reset_run()
        self.state = LIGHTS
        music_stop()
        SND_IGNITION.play()

    def update(self, dt):
        if self.state == MENU:
            self.menu_t += dt
            self.track.advance(kmh_to_ms(60) * PX_PER_M * dt)
            if self.menu_t > 1.6 and not self.intro2_played:
                SND_INTRO2.play()
                self.intro2_played = True

        elif self.state == LIGHTS:
            self.lights_t += dt
            if self.lights_t >= 5 * 0.7 + self.lights_hold:
                self.state = RACE
                music_play()
                self.pops.add("GO!", W // 2, H // 2 - 40, GREEN)

        elif self.state == RACE:
            self.update_race(dt)

        elif self.state == CRASHING:
            self.crash_t += dt
            self.shake = max(0.0, self.shake - 30 * dt)
            self.fx.update(dt)
            if self.crash_t > 1.1:
                self.state = RESULTS

        self.pops.update(dt)

    def update_race(self, dt):
        keys = pygame.key.get_pressed()

        # reading the key state every frame instead of KEYDOWN/KEYUP events.
        # the old version stopped the car when you released LEFT while still
        # holding RIGHT, since any KEYUP zeroed the velocity
        steer = 0
        if keys[pygame.K_LEFT] or keys[pygame.K_a]:
            steer -= 1
        if keys[pygame.K_RIGHT] or keys[pygame.K_d]:
            steer += 1
        throttle = keys[pygame.K_UP] or keys[pygame.K_w]
        braking = keys[pygame.K_DOWN] or keys[pygame.K_s]
        if (keys[pygame.K_LSHIFT] or keys[pygame.K_RSHIFT]) and self.drs >= 1.0 and self.drs_t <= 0:
            self.drs_t = DRS_TIME
            self.drs = 0.0
            self.pops.add("DRS OPEN", self.car_x, self.car_y - 100, BLUE)

        self.t_race += dt
        prog = self.progress()

        # longitudinal: force balance, the player controls throttle and brake
        self.throttle_on = throttle
        self.brake_on = braking
        self.v = self.longitudinal_step(self.v, throttle, braking, self.drs_t > 0, self.on_grass, dt)
        if self.drs_t > 0:
            self.drs_t -= dt

        # lateral: semi-implicit Euler (velocity first, then position).
        # on grass the tyres have less grip, so less steering authority
        grip = 0.55 if self.on_grass else 1.0
        ax = steer * STEER_ACC * grip - LAT_DAMP * self.car_vx
        self.car_vx += ax * dt
        self.car_x += self.car_vx * dt

        scroll = self.v * PX_PER_M * dt
        self.track.advance(scroll)
        self.dist += self.v * dt

        mult = 2 if self.drs_t > 0 else 1
        self.score += self.v * dt / 10 * mult
        kmh = self.v * 3.6
        self.top_kmh = max(self.top_kmh, kmh)

        self._tel_acc += dt
        if self._tel_acc >= 0.1:
            self._tel_acc -= 0.1
            self.telemetry.append((self.t_race, kmh))

        # engine sound follows rpm
        _, rpm = engine_state(self.v)
        music_volume(0.25 + 0.75 * (rpm - RPM_IDLE) / (RPM_LIMIT - RPM_IDLE))

        cr = self.car_rect()

        # run-off and walls. kerbs are fine, all four wheels on grass is not
        self.on_grass = cr.right < ROAD_L - KERB_W + 20 or cr.left > ROAD_R + KERB_W - 20
        on_kerb = cr.left < ROAD_L or cr.right > ROAD_R
        if self.on_grass:
            self.shake = 3.0
            if random.random() < 0.6:
                self.fx.emit(cr.centerx + random.uniform(-20, 20), cr.bottom - 6, 1,
                             (120, 95, 60), spd=(20, 80), life=(0.3, 0.6), size=4)
        elif on_kerb:
            self.shake = 1.5
        else:
            self.shake = 0.0

        if cr.left < BARRIER or cr.right > W - BARRIER:
            self.crash((cr.left if cr.left < BARRIER else cr.right, cr.centery - 30))
            return

        # traffic
        closing_speed = self.v * (1 - TRAFFIC_RATIO) * PX_PER_M
        closing_px = closing_speed * dt
        if self.t_race > 1.5:      # let the car get going after the start
            self.spawner.update(closing_px, closing_speed, prog, self.cars, self.car_x)

        img, mask = self.car_sprite()
        for c in self.cars:
            c.update(closing_px)

            # pixel accurate hit test. the old check compared only the
            # obstacle's bottom edge to the car's top, so a car that had
            # already gone past you could still "hit" you from behind
            off = (c.rect.x - cr.x, c.rect.y - cr.y)
            if mask.overlap(MASK_OBST, off):
                self.crash(((cr.centerx + c.rect.centerx) // 2, max(cr.top, c.rect.top) + 20))
                return

            if c.rect.bottom > cr.top and c.rect.top < cr.bottom:
                clear = max(c.rect.left - cr.right, cr.left - c.rect.right)
                c.min_clear = min(c.min_clear, clear)

            if not c.passed and c.rect.top > cr.bottom:
                c.passed = True
                self.overtakes += 1
                if self.drs_t <= 0:
                    self.drs = min(1.0, self.drs + DRS_PER_PASS)
                if c.min_clear < NEAR_MISS_PX:
                    self.near_misses += 1
                    self.score += NEAR_MISS_PTS * mult
                    self.pops.add("CLOSE CALL +%d" % (NEAR_MISS_PTS * mult),
                                  cr.centerx, cr.top - 20, YELLOW)

        self.cars = [c for c in self.cars if c.rect.top < H + 20]
        self.fx.update(dt, scroll * 0.6)

    def crash(self, pos):
        self.state = CRASHING
        self.crash_t = 0.0
        self.shake = 18.0
        music_stop()
        SND_CRASH.play()
        x, y = pos
        self.fx.emit(x, y, 45, (255, 200, 60), spd=(150, 520), life=(0.2, 0.7), size=3)
        self.fx.emit(x, y, 25, (90, 90, 90), spd=(30, 140), life=(0.6, 1.2), size=7)
        if int(self.score) > self.best:
            self.best = int(self.score)
            self.new_best = True
            save_best(self.best)    # only once per run, not every frame

    # -------- input

    def handle(self, ev):
        if ev.type == pygame.QUIT:
            return False

        if self.state == MENU:
            ready = self.menu_t > 1.6
            if ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE:
                    return False
                if not ready:
                    self.menu_t = 1.6      # any key skips the intro
                elif ev.key in (pygame.K_SPACE, pygame.K_RETURN):
                    self.start_lights()
            if ready and self.btn_start.clicked(ev):
                self.start_lights()
            if ready and not IS_WEB and self.btn_quit.clicked(ev):
                return False

        elif self.state in (RACE, LIGHTS):
            if ev.type == pygame.KEYDOWN and ev.key in (pygame.K_SPACE, pygame.K_p, pygame.K_ESCAPE):
                self.paused_from = self.state
                self.state = PAUSED
                music_pause(True)

        elif self.state == PAUSED:
            if ev.type == pygame.KEYDOWN:
                if ev.key in (pygame.K_SPACE, pygame.K_p):
                    self.state = self.paused_from
                    music_pause(False)
                elif ev.key == pygame.K_ESCAPE:
                    self.to_menu()
            # losing focus while paused shouldn't matter

        elif self.state == RESULTS:
            if ev.type == pygame.KEYDOWN:
                if ev.key in (pygame.K_SPACE, pygame.K_RETURN):
                    self.start_lights()
                elif ev.key == pygame.K_ESCAPE:
                    self.to_menu()
            if self.btn_retry.clicked(ev):
                self.start_lights()
            if self.btn_menu.clicked(ev):
                self.to_menu()

        # auto pause when the window loses focus mid race
        if ev.type == pygame.WINDOWFOCUSLOST and self.state == RACE:
            self.paused_from = RACE
            self.state = PAUSED
            music_pause(True)
        return True

    def to_menu(self):
        music_stop()
        self.reset_run()
        self.state = MENU
        self.menu_t = 1.6

    # -------- drawing

    def draw(self):
        w = self.world
        self.track.draw(w)

        if self.state == MENU:
            # intro: a backmarker drives up the screen, then the title comes in
            y = H + 40 - self.menu_t * 480
            if y > -IMG_OBST.get_height():
                w.blit(IMG_OBST, IMG_OBST.get_rect(midtop=(W // 2, y)))
        else:
            for c in self.cars:
                c.draw(w)
            if self.drs_t > 0 and self.state == RACE:
                self.draw_speed_lines(w)
            img, _ = self.car_sprite()
            w.blit(img, self.car_rect())

        self.fx.draw(w)

        ox = oy = 0
        if self.shake > 0:
            ox = random.uniform(-self.shake, self.shake)
            oy = random.uniform(-self.shake, self.shake)
        screen.fill(BLACK)
        screen.blit(w, (ox, oy))

        if self.state == MENU:
            self.draw_menu()
        elif self.state == LIGHTS:
            self.draw_hud()
            self.draw_lights()
        elif self.state in (RACE, CRASHING):
            self.draw_hud()
        elif self.state == PAUSED:
            self.draw_hud()
            dim(screen, 140)
            text(screen, "PAUSED", F_BIG, WHITE, center=(W // 2, H // 2 - 20))
            text(screen, "SPACE resume   ESC menu", F_SMALL, GREY, center=(W // 2, H // 2 + 30))
        elif self.state == RESULTS:
            self.draw_results()

        self.pops.draw(screen)
        pygame.display.flip()

    def draw_speed_lines(self, surf):
        for _ in range(10):
            x = random.uniform(ROAD_L, ROAD_R)
            y = random.uniform(0, H)
            pygame.draw.line(surf, (200, 220, 255), (x, y), (x, y + random.uniform(30, 70)), 1)

    def draw_menu(self):
        a = clamp((self.menu_t - 1.0) / 0.6, 0, 1)
        if a <= 0:
            return
        dim(screen, int(120 * a))
        ty = 170 - (1 - a) * 60
        text(screen, "F1 RACE ROAD", F_HUGE, WHITE, center=(W // 2, ty))
        pygame.draw.rect(screen, RED, (W // 2 - 150, ty + 42, 300, 5))
        if self.menu_t > 1.6:
            text(screen, "BEST  %d" % self.best, F_MED, YELLOW, center=(W // 2, 300))
            self.btn_start.draw(screen)
            if not IS_WEB:            # a browser tab can't be "quit"
                self.btn_quit.draw(screen)
            lines = ["W: throttle     S: brake     A/D: steer",
                     "SHIFT: DRS when the meter is full     SPACE: pause"]
            for i, l in enumerate(lines):
                text(screen, l, F_TINY, GREY, center=(W // 2, 560 + i * 22))

    def draw_lights(self):
        # five columns, one light every 0.7 s, all off after a random hold
        n_on = min(5, int(self.lights_t / 0.7) + 1)
        panel = pygame.Rect(0, 0, 5 * 64 + 20, 90)
        panel.center = (W // 2, 150)
        pygame.draw.rect(screen, (15, 15, 18), panel, border_radius=10)
        for i in range(5):
            cx = panel.x + 42 + i * 64
            for row in (0, 1):
                cy = panel.y + 25 + row * 40
                lit = i < n_on
                col = RED if lit else (55, 15, 15)
                pygame.draw.circle(screen, col, (cx, cy), 15)
                if lit:
                    pygame.draw.circle(screen, (255, 140, 140), (cx - 4, cy - 4), 4)

    def draw_hud(self):
        kmh = self.v * 3.6
        gear, rpm = engine_state(self.v)

        # left: score
        text(screen, "SCORE", F_TINY, GREY, topleft=(24, 10))
        text(screen, "%d" % self.score, F_MONO, WHITE, topleft=(24, 26))
        text(screen, "BEST %d" % max(self.best, int(self.score)), F_TINY, YELLOW, topleft=(24, 60))
        text(screen, "%.2f km" % (self.dist / 1000), F_TINY, GREY, topleft=(24, 80))

        # right: speed / gear / shift lights, like the steering wheel display
        rx = W - 24
        text(screen, "%3d" % kmh, F_MONO, WHITE, topright=(rx - 50, 26))
        text(screen, "km/h", F_TINY, GREY, topright=(rx - 50, 58))
        text(screen, str(gear), F_BIG, YELLOW, topright=(rx, 14))

        n_led = 10
        lit = int(clamp((rpm - 8000) / (RPM_SHIFT - 8000), 0, 1) * n_led + 0.5)
        for i in range(n_led):
            c = GREEN if i < 4 else RED if i < 7 else BLUE
            if i >= lit:
                c = (40, 40, 45)
            pygame.draw.circle(screen, c, (W - 200 + i * 18, 12), 5)
        text(screen, "%5d rpm" % rpm, F_MONO_S, (190, 195, 205), topright=(rx, 78))

        # bottom left: DRS meter
        bx, by, bw, bh = 24, H - 36, 140, 14
        pygame.draw.rect(screen, (20, 20, 25), (bx - 2, by - 2, bw + 4, bh + 4), border_radius=4)
        if self.drs_t > 0:
            fill, col, label = self.drs_t / DRS_TIME, BLUE, "DRS OPEN  x2"
        else:
            fill = self.drs
            col = GREEN if self.drs >= 1 else (90, 110, 130)
            label = "DRS READY - SHIFT" if self.drs >= 1 else "DRS"
        pygame.draw.rect(screen, col, (bx, by, int(bw * fill), bh), border_radius=3)
        text(screen, label, F_TINY, WHITE, topleft=(bx, by - 20))

        # bottom right: pedal lights, like a telemetry trace of the driver inputs
        px = W - 24
        for label, on, col in (("BRK", self.brake_on, RED), ("THR", self.throttle_on, GREEN)):
            r = pygame.Rect(px - 46, H - 38, 46, 22)
            pygame.draw.rect(screen, col if on else (35, 38, 44), r, border_radius=4)
            text(screen, label, F_TINY, WHITE if on else GREY, center=r.center, shadow=False)
            px -= 54

        if self.on_grass and self.state == RACE:
            text(screen, "OFF TRACK", F_MED, YELLOW, center=(W // 2, 40))

    def draw_results(self):
        dim(screen, 175)
        text(screen, "CRASHED", F_BIG, RED, center=(W // 2, 52))
        if self.new_best:
            text(screen, "NEW BEST", F_MED, YELLOW, center=(W // 2, 94))

        rows = [("Score", "%d" % self.score),
                ("Best", "%d" % self.best),
                ("Distance", "%.2f km" % (self.dist / 1000)),
                ("Top speed", "%d km/h" % self.top_kmh),
                ("Overtakes", "%d" % self.overtakes),
                ("Close calls", "%d" % self.near_misses),
                ("Time", "%.1f s" % self.t_race)]
        for i, (k, v) in enumerate(rows):
            col = i % 2
            row = i // 2
            x = 110 + col * 260
            y = 130 + row * 32
            text(screen, k, F_SMALL, GREY, topleft=(x, y))
            text(screen, v, F_SMALL, WHITE, topright=(x + 220, y))

        self.draw_telemetry(pygame.Rect(80, 270, W - 140, 270))
        self.btn_retry.draw(screen)
        self.btn_menu.draw(screen)

    def draw_telemetry(self, r):
        # speed trace of the run, the same kind of plot a race engineer
        # looks at first after a session
        pygame.draw.rect(screen, (14, 16, 20), r)
        pygame.draw.rect(screen, (60, 65, 75), r, 1)
        text(screen, "SPEED TRACE   v [km/h] vs t [s]", F_TINY, GREY, topleft=(r.x + 8, r.y + 7), shadow=False)

        data = self.telemetry
        if len(data) < 2:
            text(screen, "not enough data", F_SMALL, GREY, center=r.center, shadow=False)
            return

        t_max = max(data[-1][0], 1.0)
        v_top = max(100, int(math.ceil(max(v for _, v in data) / 50.0)) * 50)
        plot = pygame.Rect(r.x + 46, r.y + 34, r.w - 62, r.h - 62)

        for v in range(0, v_top + 1, 50):
            y = plot.bottom - v / v_top * plot.height
            pygame.draw.line(screen, (35, 38, 45), (plot.x, y), (plot.right, y))
            text(screen, str(v), F_TINY, GREY, topright=(plot.x - 6, y - 7), shadow=False)
        step = 5 if t_max < 40 else 10 if t_max < 120 else 30
        t = 0
        while t <= t_max:
            x = plot.x + t / t_max * plot.width
            pygame.draw.line(screen, (35, 38, 45), (x, plot.y), (x, plot.bottom))
            text(screen, str(int(t)), F_TINY, GREY, center=(x, plot.bottom + 12), shadow=False)
            t += step

        pts = [(plot.x + t / t_max * plot.width, plot.bottom - v / v_top * plot.height)
               for t, v in data]
        pygame.draw.lines(screen, (255, 70, 70), False, pts, 2)

        # mark the top speed point
        i_top = max(range(len(data)), key=lambda i: data[i][1])
        px, py = pts[i_top]
        pygame.draw.circle(screen, YELLOW, (int(px), int(py)), 4)
        text(screen, "%d" % data[i_top][1], F_TINY, YELLOW, center=(px, py - 12), shadow=False)


async def main():
    game = Game()
    running = True
    while running:
        # dt is clamped so a window drag or a hiccup doesn't produce one huge
        # step that teleports the car through a wall (tunneling)
        dt = min(clock.tick(FPS) / 1000.0, 1 / 30)
        for ev in pygame.event.get():
            if not game.handle(ev) and not IS_WEB:
                running = False
        game.update(dt)
        game.draw()
        # hand control back to the browser once per frame (no-op on desktop)
        await asyncio.sleep(0)
    pygame.quit()
    sys.exit()


if __name__ == "__main__":
    asyncio.run(main())
