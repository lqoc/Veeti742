"""GrokBot showreel — 3D motion graphics renderer. frame(t) -> RGB uint8 1080x1920."""
import math, os, sys
import numpy as np, skia
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import ad
from ad import (clamp, lin, mix, eo_expo, eo_cubic, eo_quart, ei_cubic, eio_cubic, eio_quint, eio_expo, env, col, draw_text,
                text_w, font, glow, rrect, LOGO, TGLOGO, clip_frame, SAMP, WHITE, ICE, GRAY, TG, reveal_words, label, blob)
from core3d import *

W, H, FPS, DUR = 1920, 1080, 60, 52.0
DEEP = (3, 5, 9)
def ei_quad(x): return x * x
def eo_back(x, s=1.4): x -= 1; return 1 + (s + 1) * x ** 3 + s * x ** 2
def smooth(x): return x * x * (3 - 2 * x)
def lerp3(a, b, k): return tuple(a[i] + (b[i] - a[i]) * k for i in range(3))
def P(t): return clamp((t - ad._DT[0] - 8.0) / 31.0)   # data sampled at frame centre          # ledger playback progress through the product shots

# ------------------------------------------------------------------ space background
rng = np.random.default_rng(21)
NPART = 520
PX = rng.uniform(-3600, 3600, NPART); PY = rng.uniform(-2200, 420, NPART); PZ = rng.uniform(0, 9000, NPART)
PPH = rng.uniform(0, 6.28, NPART); PSZ = rng.uniform(0.6, 1.8, NPART)

def draw_space(c, t, cam, accent=ICE, grid=0.0, floor_y=300.0, energy=1.0, nebula=1.0, grid_rgb=None):
    c.clear(col(DEEP))
    # nebula pools (screen space, slowly drifting)
    glow(c, 960 + 300 * math.sin(t * .23), 380 + 80 * math.cos(t * .19), 1250, lerp3((20, 34, 58), accent, 0.25), 0.55 * nebula)
    glow(c, 300 + 160 * math.cos(t * .17), 860, 900, (14, 24, 44), 0.5 * nebula)
    glow(c, 1650 + 120 * math.sin(t * .21), 760, 820, lerp3((18, 28, 50), accent, 0.35), 0.38 * nebula)
    # floor grid
    if grid > 0:
        gc = grid_rgb or lerp3(ICE, accent, 0.5)
        zc = cam.pos[2]; z0 = math.floor((zc - 200) / 250) * 250; z1 = z0 + 7000
        segs = []
        for x in range(-4500, 4501, 250): segs.append(((x, floor_y, z0), (x, floor_y, z1)))
        for z in np.arange(z0, z1, 250): segs.append(((-4500, floor_y, z), (4500, floor_y, z)))
        for A, B in segs:
            A = np.array(A, float); B = np.array(B, float)
            za, zb = cam.to_cam(A)[2], cam.to_cam(B)[2]
            if za < 60 and zb < 60: continue
            if za < 60: A = A + (B - A) * ((60 - za) / (zb - za)); za = 60
            if zb < 60: B = B + (A - B) * ((60 - zb) / (za - zb)); zb = 60
            (pa, _), (pb, _) = cam.proj(A), cam.proj(B)
            fa = math.exp(-za / 2600) * grid; fb = math.exp(-zb / 2600) * grid
            if max(fa, fb) < 0.01: continue
            p = skia.Paint(AntiAlias=True, Style=skia.Paint.kStroke_Style, StrokeWidth=1.0)
            p.setShader(skia.GradientShader.MakeLinear([skia.Point(*pa), skia.Point(*pb)], [col(gc, 0.30 * fa), col(gc, 0.30 * fb)]))
            c.drawLine(float(pa[0]), float(pa[1]), float(pb[0]), float(pb[1]), p)
        # horizon haze
        hz, _ = cam.proj(np.array([0, floor_y, cam.pos[2] + 9000.0]))
        hp = skia.Paint(AntiAlias=True)
        hp.setShader(skia.GradientShader.MakeLinear([skia.Point(0, hz[1] - 120), skia.Point(0, hz[1] + 160)],
                                                    [col(accent, 0), col(accent, 0.10 * grid), col(DEEP, 0)], [0, .45, 1]))
        c.drawRect(skia.Rect(0, hz[1] - 120, W, hz[1] + 160), hp)
    # particles (wrapped around camera depth)
    zr = ((PZ - cam.pos[2] - 60) % 9000) + cam.pos[2] + 60
    Pw = np.stack([PX + cam.pos[0] * 0.0, PY, zr], -1)
    scr, z = cam.proj(Pw)
    tw = 0.55 + 0.45 * np.sin(t * 1.7 + PPH)
    for i in np.argsort(-z):
        zi = z[i]
        if zi < 80: continue
        x, y = scr[i]
        if x < -50 or x > W + 50 or y < -50 or y > H + 50: continue
        r = PSZ[i] * 900 / zi * 2.2
        a = min(1.0, 900 / zi) * 0.75 * tw[i] * energy * min(1, (9000 - (zi - 60)) / 2500)
        if a < 0.02: continue
        p = skia.Paint(AntiAlias=True, Color=col(lerp3(WHITE, accent, 0.45) if i % 3 else WHITE, a))
        if r > 3.2:
            p.setMaskFilter(skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, r * 0.6)); a *= 0.5
        c.drawCircle(float(x), float(y), float(min(r, 22)), p)

def vignette(c, k=0.68):
    p = skia.Paint(AntiAlias=True)
    p.setShader(skia.GradientShader.MakeRadial(skia.Point(W / 2, H / 2), 1180, [col((0, 0, 0), 0), col((0, 0, 0), 0), col((0, 0, 0), k)], [0, .5, 1]))
    c.drawRect(skia.Rect(0, 0, W, H), p)

def flash(c, k, rgb=WHITE):
    if k <= 0: return
    p = skia.Paint(Color=col(rgb, clamp(k))); p.setBlendMode(skia.BlendMode.kPlus); c.drawRect(skia.Rect(0, 0, W, H), p)

def rays(c, x, y, a, rgb, t, n=14, r=1300, spin=0.08):
    if a <= 0.003: return
    for i in range(n):
        ang = i / n * 2 * math.pi + t * spin
        w_ = 0.07 + 0.05 * math.sin(i * 2.3)
        pth = skia.Path(); pth.moveTo(x, y)
        pth.lineTo(x + r * math.cos(ang - w_), y + r * math.sin(ang - w_)); pth.lineTo(x + r * math.cos(ang + w_), y + r * math.sin(ang + w_)); pth.close()
        p = skia.Paint(AntiAlias=True); p.setBlendMode(skia.BlendMode.kPlus)
        p.setShader(skia.GradientShader.MakeRadial(skia.Point(x, y), r, [col(rgb, a * (0.5 + 0.5 * math.sin(i * 1.7 + t))), col(rgb, 0)]))
        c.drawPath(pth, p)

def floor_ring(c, cam, cx, cz, r, floor_y, a, rgb, w=2.0):
    if a <= 0.003: return
    ang = np.linspace(0, 2 * math.pi, 97)
    Pw = np.stack([cx + r * np.cos(ang), np.full_like(ang, floor_y), cz + r * np.sin(ang)], -1)
    scr, z = cam.proj(Pw)
    if (z < 60).any(): return
    p = skia.Paint(AntiAlias=True, Style=skia.Paint.kStroke_Style, StrokeWidth=w, Color=col(rgb, a)); p.setBlendMode(skia.BlendMode.kPlus)
    c.drawPath(poly_path(scr), p)
    p2 = skia.Paint(AntiAlias=True, Style=skia.Paint.kStroke_Style, StrokeWidth=w * 6, Color=col(rgb, a * 0.25)); p2.setBlendMode(skia.BlendMode.kPlus)
    p2.setMaskFilter(skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, 8)); c.drawPath(poly_path(scr), p2)

# ------------------------------------------------------------------ extruded tiles
def face_logo(c, sw, sh, r):
    c.save(); c.clipRRect(rrect(0, 0, sw, sh, r), True)
    c.drawImageRect(LOGO, skia.Rect(0, 0, LOGO.width(), LOGO.height()), skia.Rect(0, 0, sw, sh), SAMP, skia.Paint(AntiAlias=True)); c.restore()
def face_tg(c, sw, sh, r):
    c.drawImageRect(TGLOGO, skia.Rect(0, 0, TGLOGO.width(), TGLOGO.height()), skia.Rect(0, 0, sw, sh), SAMP, skia.Paint(AntiAlias=True))

def extruded(c, cam, C, R, w, h, depth, radius, front, back=None, side0=(40, 42, 48), side1=(150, 152, 160), alpha=1.0,
             sheen=None, rim=ICE, slices=None):
    C = np.asarray(C, float); n = R @ np.array([0, 0, -1.0])
    ns = slices or max(6, int(depth / 2.2))
    facing = float(np.dot(n, cam.pos - C)) > 0
    order = range(ns + 1) if facing else range(ns, -1, -1)
    S = 400.0; sw, sh = S * w / max(w, h), S * h / max(w, h); rr = radius * sw / w
    for k in order:
        off = -depth / 2 + depth * k / ns
        pl = plane(cam, C + n * off, R, w, h, sw, sh)
        if pl is None: continue
        sc = lerp3(side0, side1, (k / ns) ** 1.5 if facing else 1 - (k / ns) ** 1.5)
        c.save(); c.concat(pl[0]); c.drawRRect(rrect(0, 0, sw, sh, rr), skia.Paint(AntiAlias=True, Color=col(sc, alpha))); c.restore()
    if facing:
        pl = plane(cam, C + n * depth / 2, R, w, h, sw, sh)
        def fn(cc):
            front(cc, sw, sh, rr)
            if sheen is not None and 0 < sheen < 1:
                gx = mix(-sw * 0.9, sw * 1.9, sheen)
                gp = skia.Paint(AntiAlias=True); gp.setBlendMode(skia.BlendMode.kPlus)
                gp.setShader(skia.GradientShader.MakeLinear([skia.Point(gx - sw * .3, 0), skia.Point(gx + sw * .3, sh)], [col(WHITE, 0), col(WHITE, .45), col(WHITE, 0)]))
                cc.save(); cc.clipRRect(rrect(0, 0, sw, sh, rr), True); cc.drawRect(skia.Rect(0, 0, sw, sh), gp); cc.restore()
            st = skia.Paint(AntiAlias=True, Style=skia.Paint.kStroke_Style, StrokeWidth=2.0)
            st.setShader(skia.GradientShader.MakeLinear([skia.Point(0, 0), skia.Point(sw, sh)], [col(WHITE, 0.55), col(rim, 0.05), col(rim, 0.35)], [0, .55, 1]))
            cc.drawRRect(rrect(1, 1, sw - 2, sh - 2, rr), st)
        on_plane(c, pl, fn, alpha=alpha)
    elif back is not None:
        Rb = R @ rot(math.pi, 0, 0)
        pl = plane(cam, C - n * depth / 2, Rb, w, h, sw, sh)
        on_plane(c, pl, lambda cc: back(cc, sw, sh, rr), alpha=alpha)

def phone_back(s):
    def fn(cc, sw, sh, rr):
        cc.save(); cc.clipRRect(rrect(0, 0, sw, sh, rr), True); cc.scale(sw / 390, sh / 694); draw_ui(cc, s); cc.restore()
    return fn

# ------------------------------------------------------------------ text helpers
def outline_text(c, s, x, y, name, size, rgb, a, sw=2.0, align="left", fill_a=0.0):
    if a <= 0.003: return
    w = text_w(s, name, size); x0 = x - w / 2 if align == "center" else x - w if align == "right" else x
    b = blob(s, name, size); asc = font(name, size).getMetrics().fAscent
    if fill_a > 0: c.drawTextBlob(b, x0, y + asc, skia.Paint(AntiAlias=True, Color=col(rgb, a * fill_a)))
    c.drawTextBlob(b, x0, y + asc, skia.Paint(AntiAlias=True, Style=skia.Paint.kStroke_Style, StrokeWidth=sw, Color=col(rgb, a)))

def letters3d(c, cam, s, cx, cy, name, size, t, t0, rgb=WHITE, stagger=0.035, dur=0.95, seed=1, out=0.0, grad=None):
    """Per-glyph 3D fly-in. out>0 flies the letters through the camera."""
    f = font(name, size); gl = f.textToGlyphs(s); ws = f.getWidths(gl)
    total = sum(ws); x = cx - total / 2
    r = np.random.default_rng(seed)
    asc = -f.getMetrics().fAscent
    items = []
    for i, (ch, gw) in enumerate(zip(s, ws)):
        rx, ry, rz = r.uniform(-1, 1, 3); ox, oy, oz = r.uniform(-1, 1), r.uniform(-1, 1), r.uniform(0.3, 1)
        k = eo_expo(lin(t, t0 + i * stagger, t0 + i * stagger + dur))
        ko = ei_cubic(lin(out, i / len(s) * 0.35, i / len(s) * 0.35 + 0.65)) if out > 0 else 0
        if k <= 0 or ch == " ": x += gw; continue
        Cw = np.array([x + gw / 2 - 960 + (1 - k) * ox * 700, cy - 540 + (1 - k) * oy * 380, (1 - k) * (1800 + 900 * oz)])
        Cw += np.array([(x + gw / 2 - 960) * 0.8 * ko, (cy - 540) * 0.6 * ko + oy * 200 * ko, -1250 * ko])
        R = rot((1 - k) * ry * 1.6 + ko * ry * 0.8, (1 - k) * rx * 1.0, (1 - k) * rz * 0.5)
        pw, ph = gw * 1.2, size * 1.35
        pl = plane(cam, Cw, R, pw, ph, pw, ph)
        if pl is None: x += gw; continue
        a = clamp(k * 1.5) * (1 - clamp((ko - 0.75) / 0.25))
        items.append((pl[1], pl, ch, gw, pw, ph, a))
        x += gw
    for _, pl, ch, gw, pw, ph, a in sorted(items, key=lambda v: -v[0]):
        def fn(cc, ch=ch, gw=gw, pw=pw, ph=ph, a=a):
            p = skia.Paint(AntiAlias=True, Color=col(rgb, a))
            if grad: p.setShader(skia.GradientShader.MakeLinear([skia.Point(0, 0), skia.Point(0, ph)], [col(grad[0], a), col(grad[1], a)]))
            cc.drawString(ch, (pw - gw) / 2, ph * 0.5 + asc * 0.36, f, p)
        on_plane(c, pl, fn)

def glitch_text(c, s, x, y, name, size, k, a=1.0, align="center", tracking=0.0):
    """RGB-split reveal: k 0->1 converges channels."""
    off = (1 - k) * 22
    for rgb, dx in (((255, 40, 60), -off), ((40, 255, 140), 0), ((60, 120, 255), off)):
        c.save(); c.translate(dx, 0)
        p_ = skia.Paint(); p_.setBlendMode(skia.BlendMode.kPlus)
        c.saveLayer(None, p_)
        draw_text(c, s, x, y, name, size, rgb, a, align, tracking=tracking)
        c.restore(); c.restore()

# ------------------------------------------------------------------ subtitles (burned-in) + SRT
SUBS = [(0.9, 3.6, "The market never sleeps."),
        (5.3, 7.5, "Meet GrokBot."),
        (8.5, 10.9, "Your whole trading desk, in one app."),
        (11.2, 13.6, "Balance, chart, agents and activity — live."),
        (14.5, 16.4, "The Scanner hunts for new pairs."),
        (16.95, 18.9, "The Analyst scores every signal."),
        (19.45, 21.4, "The Executor opens and closes positions."),
        (21.95, 23.8, "Risk runs the checks — and the stop-loss."),
        (24.4, 27.0, "Every position, tracked from entry to exit."),
        (27.2, 29.9, "Every win — and every stop-loss — on the record."),
        (30.6, 33.0, "Every alert, straight to your phone."),
        (33.2, 35.0, "Profit taken or stop triggered — you’ll know."),
        (35.7, 38.6, "One dashboard for all of it."),
        (41.2, 44.4, "GrokBot is now on Telegram."),
        (45.6, 50.4, "Join at t.me/grokbotting")]

def subtitles(c, t):
    for t0, t1, s in SUBS:
        if t0 - 0.05 <= t <= t1 + 0.35:
            words = s.split(" "); size = 38; name = "InterDisplay-Medium"
            total = text_w(s, name, size); x0 = 960 - total / 2; y = 992
            out = eio_cubic(lin(t, t1, t1 + 0.3))
            # soft legibility band
            bp = skia.Paint(AntiAlias=True)
            bp.setShader(skia.GradientShader.MakeRadial(skia.Point(960, y - 12), total * 0.62 + 120, [col((0, 0, 0), 0.55 * (1 - out)), col((0, 0, 0), 0)]))
            c.save(); c.scale(1, 0.22); c.drawCircle(960, (y - 12) / 0.22, total * 0.62 + 120, bp); c.restore()
            dur = min(0.9, (t1 - t0) * 0.45)
            for i, wd in enumerate(words):
                pre = " ".join(words[:i]) + (" " if i else "")
                wx = x0 + text_w(pre, name, size)
                k = eo_cubic(lin(t, t0 + dur * i / max(1, len(words)), t0 + dur * i / max(1, len(words)) + 0.32))
                if k <= 0: continue
                a = k * (1 - out)
                sh = skia.Paint(AntiAlias=True, Color=col((0, 0, 0), 0.7 * a)); sh.setMaskFilter(skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, 6))
                b = blob(wd, name, size); asc = font(name, size).getMetrics().fAscent
                c.drawTextBlob(b, wx, y + (1 - k) * 10 + asc + 2, sh)
                hl = "t.me/grokbotting" in wd
                draw_text(c, wd, wx, y + (1 - k) * 10, name, size, (150, 220, 255) if hl else WHITE, a, blur=(1 - k) * 3)

def write_srt(path):
    def ts(x):
        ms = int(round(x * 1000)); return "%02d:%02d:%02d,%03d" % (ms // 3600000, ms // 60000 % 60, ms // 1000 % 60, ms % 1000)
    with open(path, "w") as f:
        for i, (a, b, s) in enumerate(SUBS, 1): f.write("%d\n%s --> %s\n%s\n\n" % (i, ts(a), ts(b + 0.3), s))

# ------------------------------------------------------------------ HUD
CHAPTERS = [(0, 8, "01", "INTRO"), (8, 14.1, "02", "THE DESK"), (14.1, 24.05, "03", "AGENTS"), (24.05, 30.2, "04", "POSITIONS"),
            (30.2, 35.25, "05", "ALERTS"), (35.25, 40.0, "06", "DASHBOARD"), (40.0, 52, "07", "TELEGRAM")]
def hud(c, t):
    a = env(t, 0.6, 1.4, 50.6, 51.4)
    if a <= 0: return
    p = skia.Paint(AntiAlias=True, Style=skia.Paint.kStroke_Style, StrokeWidth=1.2, Color=col(WHITE, 0.28 * a))
    m, L_ = 40, 28
    for (x, y, sx, sy) in ((m, m, 1, 1), (W - m, m, -1, 1), (m, H - m, 1, -1), (W - m, H - m, -1, -1)):
        c.drawLine(x, y, x + sx * L_, y, p); c.drawLine(x, y, x, y + sy * L_, p)
    for a0, a1, num, name in CHAPTERS:
        if a0 <= t < a1:
            k = eo_expo(lin(t, a0 + 0.15, a0 + 0.9))
            draw_text(c, num, W - 76, 76, "Inter-SemiBold", 13, (150, 220, 255), a * k, "right", tracking=0.2)
            draw_text(c, name, W - 112, 76, "Inter-Medium", 13, WHITE, 0.65 * a * k, "right", tracking=mix(0.6, 0.3, k))
    b = env(t, 8.3, 8.9, 39.6, 40.0)
    if b > 0:
        ad.draw_logo(c, LOGO, 92, 70, 34, b * 0.95)
        draw_text(c, "GrokBot", 120, 77, "InterDisplay-SemiBold", 21, WHITE, b * 0.9)
    d = env(t, 8.2, 8.8, 39.6, 40.0)
    if d > 0:
        draw_text(c, "ILLUSTRATIVE DATA · NOT FINANCIAL ADVICE", 64, 1046, "Inter-Medium", 13, GRAY, 0.85 * d, tracking=0.2)

# ================================================================== SHOTS
OBJ = Cam((0, 0, -1400))

def shot1(c, t):
    """0 - 4: CRT turn-on, grid flythrough, 3D kinetic type."""
    camz = -1400 + 420 * t + (180 * (t - 3.0) ** 3 if t > 3 else 0)
    cam = Cam.look((0, -230, camz), (0, 40, camz + 2400))
    draw_space(c, t, cam, accent=(74, 222, 128), grid=lin(t, 0.5, 1.6), energy=lin(t, 0.4, 1.4))
    # turn-on line
    k = env(t, 0.05, 0.25, 0.35, 0.9, eo_cubic, eo_cubic)
    if k > 0:
        w_ = W * eo_expo(lin(t, 0.05, 0.5))
        p = skia.Paint(AntiAlias=True); p.setBlendMode(skia.BlendMode.kPlus)
        p.setShader(skia.GradientShader.MakeLinear([skia.Point(960 - w_ / 2, 0), skia.Point(960 + w_ / 2, 0)], [col(ICE, 0), col(WHITE, k), col(ICE, 0)], [0, .5, 1]))
        c.drawRect(skia.Rect(960 - w_ / 2, 538, 960 + w_ / 2, 542), p); glow(c, 960, 540, 300, ICE, 0.4 * k)
    out = lin(t, 3.05, 4.0)
    letters3d(c, OBJ, "THE MARKET", 960, 470, "InterDisplay-Black", 150, t, 0.55, WHITE, seed=3, out=out)
    letters3d(c, OBJ, "NEVER SLEEPS.", 960, 625, "InterDisplay-Black", 150, t, 0.85, WHITE, seed=7, out=out, grad=((150, 255, 190), (90, 190, 255)))
    flash(c, env(t, 3.75, 3.98, 3.98, 4.0, ei_cubic, eo_cubic) * 0.9, (220, 245, 255))

def logo_rot(t):
    k = eo_expo(lin(t, 4.0, 5.7)) ; d = 1 - eio_quint(lin(t, 6.6, 7.6))
    return rot(mix(-2.4, 0, k) + 0.07 * math.sin(t * 1.3) * k * d, mix(0.5, 0, k) + 0.04 * math.sin(t * 0.9) * k * d, 0)

def shot2(c, t):
    """4 - 8: extruded logo forge + flip-morph into the phone."""
    cam = Cam.look((0, -260, -1300 + 40 * (t - 4)), (0, 0, 600))
    draw_space(c, t, cam, accent=ICE, grid=0.8, energy=1.0)
    imp = env(t, 4.0, 4.05, 4.05, 5.6, eo_cubic, eo_cubic)
    rays(c, 960, 470, 0.05 + 0.10 * imp, ICE, t)
    for d_, a_ in ((0, 0.9), (0.18, 0.5)):
        k = lin(t, 4.0 + d_, 5.6 + d_)
        if 0 < k < 1: floor_ring(c, cam, 0, 500, mix(80, 2600, eo_cubic(k)), 300, a_ * (1 - k), ICE, 2.2)
    fl = mix(1, 0, eio_quint(lin(t, 7.0, 8.0)))       # flip/morph progress inverse
    kf = eio_quint(lin(t, 7.0, 8.0))
    size = 330 * (1 + 0.04 * imp)
    w = mix(size, 390 * 1.25, kf); h = mix(size, 694 * 1.25, kf); dep = mix(80, 14, kf)
    Cw = np.array([0, mix(-70, 0, kf), mix(0, 0, kf)])
    R = logo_rot(t) @ rot(math.pi * kf, 0, 0)
    glow(c, 960, 470 + 70 * kf, 520, ICE, 0.18 + 0.2 * imp)
    pl = plane(OBJ, Cw, np.eye(3), 10, 10, 10, 10)
    s = state_at(LED, P(8.0))
    extruded(c, OBJ, Cw, R, w, h, dep, mix(size * 0.225, 48, kf), face_logo, back=phone_back(s), sheen=lin(t, 5.0, 5.8))
    # wordmark
    k = eo_expo(lin(t, 5.05, 5.8)); o = eio_cubic(lin(t, 6.8, 7.2))
    if k > 0 and o < 1:
        glitch_text(c, "GROKBOT", 960, 760, "InterDisplay-Bold", 64, k, (1 - o), "center", tracking=mix(0.9, 0.42, k))
        lw = 260 * eo_expo(lin(t, 5.4, 6.2)) * (1 - o)
        lp = skia.Paint(AntiAlias=True)
        lp.setShader(skia.GradientShader.MakeLinear([skia.Point(960 - lw, 0), skia.Point(960 + lw, 0)], [col(ICE, 0), col(ICE, .6), col(ICE, 0)], [0, .5, 1]))
        c.drawRect(skia.Rect(960 - lw, 800, 960 + lw, 801.3), lp)
    flash(c, env(t, 4.0, 4.0, 4.0, 4.5, eo_cubic, eo_cubic) * 0.8, (220, 245, 255))

# ---- shot 3: exploded UI
EXPL = [("status", 0), ("notice", 1), ("header", 2), ("balance", 3), ("chart", 4), ("tf", 5), ("agents", 6), ("activity", 7), ("nav", 8)]
TAGS = {"balance": "BALANCE", "chart": "LIVE CHART", "agents": "AGENTS", "activity": "ACTIVITY", "notice": "ALERTS"}
K = 1.25
def shot3_cam(t):
    th = mix(0, 0.66, eio_quint(lin(t, 8.6, 10.3))); th = mix(th, 0.16, eio_quint(lin(t, 12.4, 13.4)))
    ph = mix(0, 0.30, eio_quint(lin(t, 8.6, 10.3))); ph = mix(ph, 0.05, eio_quint(lin(t, 12.4, 13.4)))
    D = mix(1400, 1900, eio_quint(lin(t, 8.6, 10.3))); D = mix(D, 1450, eio_quint(lin(t, 12.4, 13.4)))
    e_ = eio_quint(lin(t, 8.9, 10.4)) * (1 - eio_quint(lin(t, 12.4, 13.3)))
    tgt = np.array([0, 60 * e_, -430.0 * e_])
    # dive into agents section
    dv = ei_cubic(lin(t, 13.35, 14.1))
    agents_c = np.array([0, (462 - 347) * K, -0.0])
    tgt = tgt + (agents_c - tgt) * eio_cubic(lin(t, 13.0, 13.7))
    D = mix(D, 160, dv)
    pos = tgt + D * np.array([math.sin(th) * math.cos(ph), -math.sin(ph), -math.cos(th) * math.cos(ph)])
    return Cam.look(pos, tgt, roll=0.04 * math.sin(t * 0.8))

def shot3(c, t):
    cam = shot3_cam(t)
    draw_space(c, t, cam, accent=(74, 222, 128), grid=0.55, floor_y=620)
    e = eio_quint(lin(t, 8.9, 10.4)) * (1 - eio_quint(lin(t, 12.4, 13.3)))
    s = state_at(LED, P(t))
    if s["notice"] is None and e > 0.05:
        s = dict(s); s["notice"] = s["closed"][-1] if s["closed"] else None
    # phone body
    Cb = np.array([0, 0, 18.0 * e])
    pl = plane(cam, Cb, np.eye(3), 390 * K + 26, 694 * K + 26, 416, 720)
    glow(c, 960, 540, 900, (74, 222, 128), 0.07)
    def body(cc):
        bp = skia.Paint(AntiAlias=True)
        bp.setShader(skia.GradientShader.MakeLinear([skia.Point(0, 0), skia.Point(416, 720)], [col((34, 38, 48)), col((10, 12, 17))]))
        cc.drawRRect(rrect(0, 0, 416, 720, 58), bp)
        cc.drawRRect(rrect(13, 13, 390, 694, 46), skia.Paint(AntiAlias=True, Color=col((0, 0, 0))))
        st = skia.Paint(AntiAlias=True, Style=skia.Paint.kStroke_Style, StrokeWidth=1.6)
        st.setShader(skia.GradientShader.MakeLinear([skia.Point(0, 0), skia.Point(300, 720)], [col(WHITE, .45), col(WHITE, .05), col(ICE, .2)], [0, .55, 1]))
        cc.drawRRect(rrect(0, 0, 416, 720, 58), st)
    sp = skia.Paint(AntiAlias=True, Color=col((0, 0, 0), 0.6)); sp.setMaskFilter(skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, 40))
    on_plane(c, pl, body)
    layers = []
    for name, i in EXPL:
        y0, y1 = [(a, b) for n, a, b in SECTIONS if n == name][0]
        ym = (y0 + y1) / 2
        Cw = np.array([0, (ym - 347) * K, -e * 95 * (i + 1) - 0.5])
        pl_ = plane(cam, Cw, np.eye(3), 390 * K, (y1 - y0) * K, 390, y1 - y0)
        if pl_ is None: continue
        layers.append((pl_[1], name, y0, y1, pl_, Cw))
    tags = []
    for depth, name, y0, y1, pl_, Cw in sorted(layers, key=lambda v: -v[0]):
        def fn(cc, name=name, y0=y0, y1=y1):
            if e > 0.01:
                cc.drawRRect(rrect(-6, -3, 402, y1 - y0 + 6, 14), skia.Paint(AntiAlias=True, Color=col((8, 10, 14), 0.88 * e)))
                cc.drawRRect(rrect(-6, -3, 402, y1 - y0 + 6, 14), stroke_paint((120, 200, 255), 1.0, 0.35 * e))
            cc.save(); cc.clipRect(skia.Rect(-6, -3, 396, y1 - y0 + 3)); cc.translate(0, -y0); draw_section(cc, name, s); cc.restore()
        on_plane(c, pl_, fn)
        if name in TAGS:
            pr, _ = cam.proj(Cw + np.array([390 * K / 2, 0, 0]))
            tags.append((pr, TAGS[name]))
    # HUD tags
    for i, (pr, nm) in enumerate(tags):
        k = eo_expo(lin(t, 9.7 + i * 0.12, 10.5 + i * 0.12)) * (1 - eio_cubic(lin(t, 12.2, 12.6)))
        if k <= 0: continue
        x, y = float(pr[0]), float(pr[1])
        lp = skia.Paint(AntiAlias=True, Color=col((150, 220, 255), 0.7 * k))
        c.drawCircle(x, y, 4, lp); c.drawLine(x, y, x + 34, y - 34, stroke_paint((150, 220, 255), 1.2, 0.6 * k))
        c.drawLine(x + 34, y - 34, x + 34 + 150 * k, y - 34, stroke_paint((150, 220, 255), 1.2, 0.6 * k))
        draw_text(c, nm, x + 40, y - 44, "Inter-SemiBold", 15, WHITE, k, tracking=0.25)
    flash(c, env(t, 13.85, 14.08, 14.08, 14.1, ei_cubic, eo_cubic) * 0.85, (200, 255, 220))

# ---- shot 4: agents ring
RING_C = np.array([0, 0, 700.0]); RING_R = 640
WIN = [(14.1, 16.45), (16.95, 18.95), (19.45, 21.45), (21.95, 23.4)]
def ring_angle(t):
    a = mix(-math.pi * 1.75, 0, eo_expo(lin(t, 14.1, 15.1)))
    for i in range(1, 4):
        a += -(math.pi / 2) * eio_quint(lin(t, WIN[i - 1][1], WIN[i][0]))
    a += -2 * math.pi * ei_cubic(lin(t, 23.4, 24.05))
    return a
def active_agent(t):
    for i in range(3):
        if t < (WIN[i][1] + WIN[i + 1][0]) / 2: return i
    return 3

def shot4(c, t):
    cam = Cam.look((80 * math.sin(t * 0.5), -330, -1150), (0, 40, 700), roll=-0.02)
    ai = active_agent(t); acc = AG[ai][1]
    draw_space(c, t, cam, accent=acc, grid=0.75, floor_y=250, nebula=1.1)
    # giant outlined agent name far behind
    for i in range(4):
        a0, a1 = WIN[i]; k = env(t, a0 - 0.15, a0 + 0.5, a1 - 0.1, a1 + 0.35)
        if k <= 0: continue
        sl = (t - a0) * 40
        outline_text(c, AG[i][0].upper(), 960 - sl + (1 - k) * 200, 395, "InterDisplay-Black", 290, AG[i][1], 0.3 * k, 2.0, "center", fill_a=0.10)
    glow(c, 960, 520, 700, acc, 0.16)
    ang = ring_angle(t); s = state_at(LED, P(t))
    # pipeline arcs with travelling particles
    arc_a = np.linspace(0, 2 * math.pi, 181)
    Pw = np.stack([RING_C[0] + (RING_R - 40) * np.sin(arc_a + ang), np.full_like(arc_a, 215.0), RING_C[2] - (RING_R - 40) * np.cos(arc_a + ang)], -1)
    scr, z = cam.proj(Pw)
    if (z > 60).all():
        for k in range(180):
            seg_i = int(k / 45); rgb = AG[seg_i % 4][1]
            p = skia.Paint(AntiAlias=True, Style=skia.Paint.kStroke_Style, StrokeWidth=2.0, Color=col(rgb, 0.45 * min(1, 1200 / z[k])))
            c.drawLine(float(scr[k][0]), float(scr[k][1]), float(scr[k + 1][0]), float(scr[k + 1][1]), p)
        for j in range(16):
            u = ((t * 0.12 + j / 16) % 1.0); idx_ = int(u * 180)
            rgb = AG[(idx_ // 45) % 4][1]
            glow(c, float(scr[idx_][0]), float(scr[idx_][1]), 16, rgb, 0.8); c.drawCircle(float(scr[idx_][0]), float(scr[idx_][1]), 2.4, skia.Paint(AntiAlias=True, Color=col(WHITE)))
    cards = []
    intro = eo_expo(lin(t, 14.1, 15.0))
    for i in range(4):
        a = ang + i * math.pi / 2
        Cw = RING_C + np.array([RING_R * math.sin(a), -60 + 40 * (1 - intro), -RING_R * math.cos(a)])
        R = rot(-a, 0, 0)
        pl_ = plane(cam, Cw, R, 145 * 3.3, 94 * 3.3, 145, 94)
        if pl_ is None: continue
        cards.append((pl_[1], i, pl_, Cw, R))
    zfront = min(v[0] for v in cards) if cards else 0
    for depth, i, pl_, Cw, R in sorted(cards, key=lambda v: -v[0]):
        hi = 1.0 if i == ai else 0.0
        hi *= 1 - lin(t, 23.4, 23.6)
        blur = clamp((depth - zfront) / 600) * 7
        dim = mix(0.5, 1.0, hi)
        # reflection
        Rr = np.diag([1, -1, 1.0]) @ R
        plr = plane(cam, np.array([Cw[0], 2 * 250 - Cw[1], Cw[2]]), np.eye(3) @ R, 145 * 3.3, 94 * 3.3, 145, 94)
        if plr is not None:
            def rf(cc, i=i):
                cc.translate(0, 94); cc.scale(1, -1); draw_agent_card(cc, i, s, spin=-t * 8)
            on_plane(c, plr, rf, blur=blur + 3, alpha=0.13 * dim * intro)
        if hi > 0: glow(c, *[float(v) for v in cam.proj(Cw)[0]], 420, AG[i][1], 0.22 * hi)
        on_plane(c, pl_, lambda cc, i=i, hi=hi: draw_agent_card(cc, i, s, spin=-t * 8, hi=hi), blur=blur, alpha=dim * clamp(intro * 1.3))
    # index label
    k = eo_expo(lin(t, 14.6, 15.3))
    draw_text(c, "AGENT  %02d / 04" % (ai + 1), 960, 175, "Inter-SemiBold", 15, AG[ai][1], k, "center", tracking=0.35)
    flash(c, env(t, 23.85, 24.04, 24.04, 24.06, ei_cubic, eo_cubic) * 0.6, (255, 255, 255))

# ---- shot 5: 3D equity ribbon flythrough
NPTS = 240
MINS = np.linspace(0, LED["minutes"], NPTS)
EQ = np.array([equity_at_minute(LED, m) for m in MINS])
LO, HI = chart_bounds(LED)
ZLEN = 6400.0; FLOOR5 = 320.0; HSCALE = 1100.0
def curve_pt(m):
    e = equity_at_minute(LED, m); return np.array([0.0, FLOOR5 - (e - LO) / (HI - LO) * HSCALE, m / LED["minutes"] * ZLEN])
CURVE = np.stack([np.zeros(NPTS), FLOOR5 - (EQ - LO) / (HI - LO) * HSCALE, MINS / LED["minutes"] * ZLEN], -1)

def shot5_cam(t):
    u = eio_cubic(lin(t, 24.05, 28.9))
    zc = mix(-200, 5000, u)
    cy = curve_pt(clamp((zc + 600) / ZLEN) * LED["minutes"])[1]
    pos = np.array([1150 + 80 * math.sin(t * 0.6), cy - 420, zc - 750])
    tgt = np.array([0, cy - 80, zc + 600])
    k = eio_quint(lin(t, 28.5, 30.2))
    pos = pos + (np.array([4300, -1600, 700]) - pos) * k
    tgt = tgt + (np.array([0, -420, 3300]) - tgt) * k
    return Cam.look(pos, tgt, roll=mix(-0.06, 0.0, k)), zc

def shot5(c, t):
    cam, zc = shot5_cam(t)
    draw_space(c, t, cam, accent=(74, 222, 128), grid=0.9, floor_y=FLOOR5, grid_rgb=(74, 222, 128))
    reveal = eo_cubic(lin(t, 24.05, 25.0))
    scr, z = cam.proj(CURVE)
    fl = CURVE.copy(); fl[:, 1] = FLOOR5
    fscr, fz = cam.proj(fl)
    # wall (quad strip)
    for i in range(NPTS - 1):
        if min(z[i], z[i + 1], fz[i], fz[i + 1]) < 60: continue
        pth = poly_path([scr[i], scr[i + 1], fscr[i + 1], fscr[i]])
        gp = skia.Paint(AntiAlias=True)
        gp.setShader(skia.GradientShader.MakeLinear([skia.Point(*scr[i]), skia.Point(*fscr[i])], [col((74, 222, 128), 0.20 * reveal), col((74, 222, 128), 0.0)]))
        c.drawPath(pth, gp)
    # line + glow
    ok = z > 60
    runs = []; cur = []
    for i in range(NPTS):
        if ok[i]: cur.append(scr[i])
        elif cur: runs.append(cur); cur = []
    if cur: runs.append(cur)
    for r_ in runs:
        if len(r_) < 2: continue
        pa = poly_path(r_, False)
        g = stroke_paint((74, 222, 128), 14, 0.25 * reveal); g.setMaskFilter(skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, 10)); g.setBlendMode(skia.BlendMode.kPlus)
        c.drawPath(pa, g); c.drawPath(pa, stroke_paint((140, 255, 180), 3.2, reveal))
    # trade pins (billboards)
    pins = []
    for tr in LED["trades"]:
        P0 = curve_pt(tr["sell"]); top = P0 + np.array([0, -230, 0])
        (p0, z0), (p1, z1) = cam.proj(P0), cam.proj(top)
        if z0 < 80 or z1 < 80: continue
        pins.append((z0, tr, P0, top, p0, p1))
    for z0, tr, P0, top, p0, p1 in sorted(pins, key=lambda v: -v[0]):
        loss = tr["profit"] < 0; cc_ = RED if loss else GREEN
        a = clamp(2400 / z0) * reveal * clamp((z0 - 650) / 450) * clamp((9000 - z0) / 2500)
        c.drawLine(float(p0[0]), float(p0[1]), float(p1[0]), float(p1[1]), stroke_paint(cc_, 1.4, 0.8 * a, ([4, 5], 0)))
        glow(c, float(p0[0]), float(p0[1]), 30, cc_, 0.6 * a)
        c.drawCircle(float(p0[0]), float(p0[1]), 4.5 * min(2, 1400 / z0), skia.Paint(AntiAlias=True, Color=col(cc_, a)))
        R = cam.R  # billboard faces camera
        pl_ = plane(cam, top + np.array([0, -30, 0]), R, 64 * 3.6, 17 * 3.6, 64, 17)
        def lab(cc, tr=tr, cc_=cc_, loss=loss):
            box(cc, 0, 0, 64, 17, 4, "#07120b"); cc.drawRRect(rrect(0, 0, 64, 17, 4), stroke_paint(cc_, .7))
            txt(cc, signed(tr["profit"]), 32, 11, 7.5, cc_, 550, "center")
        on_plane(c, pl_, lab, alpha=a)
    # HUD readout
    _, zc0 = shot5_cam(t - ad._DT[0])
    m = clamp((zc0 + 600) / ZLEN) * LED["minutes"]; m = mix(m, LED["minutes"], lin(t - ad._DT[0], 28.6, 29.6))
    e_ = equity_at_minute(LED, m)
    k = eo_expo(lin(t, 24.4, 25.2)) * (1 - eio_cubic(lin(t, 29.8, 30.15)))
    if k > 0:
        ad.glass(c, 70, 130, 420, 150, 22, k * 0.9, backdrop=False)
        draw_text(c, "TOTAL BALANCE", 98, 174, "Inter-Medium", 13, GRAY, k, tracking=0.25)
        draw_text(c, money(jround(e_)), 96, 238, "InterDisplay-SemiBold", 52, WHITE, k)
        draw_text(c, fmt_time(LED["start_minute"] + m) + "  ·  ILLUSTRATIVE", 98, 264, "Inter-Medium", 12, (74, 222, 128), 0.85 * k, tracking=0.18)

# ---- shot 6: alerts deck
ALERTS = [LED["trades"][i] for i in (3, 7, 2, 11, 12, 15, 17, 18)]
def shot6(c, t):
    cam = Cam.look((0, -330, -1450), (0, 30, 300))
    draw_space(c, t, cam, accent=(120, 190, 255), grid=0.6, floor_y=330)
    collapse = eio_quint(lin(t, 34.55, 35.15))
    items = []
    n_ar = 0
    for k_, tr in enumerate(ALERTS):
        ta = 30.45 + k_ * 0.5
        if t < ta: break
        n_ar = k_ + 1
    for k_, tr in enumerate(ALERTS[:n_ar]):
        ta = 30.45 + k_ * 0.5
        arr = eo_expo(lin(t, ta, ta + 0.6))
        j = 0.0
        for m_ in range(k_ + 1, n_ar):
            j += eo_expo(lin(t, 30.45 + m_ * 0.5, 30.45 + m_ * 0.5 + 0.6))
        Cw = np.array([0, -j * 78 + (1 - arr) * -700, j * 190 + (1 - arr) * 2600])
        Cw = Cw + (np.array([0, 0, 200]) - Cw) * collapse
        R = rot(0, -0.32 + (1 - arr) * -0.8, (1 - arr) * 0.3 * (1 if k_ % 2 else -1))
        hgt = 49 * 2.3 * (1 - collapse * 0.985)
        pl_ = plane(cam, Cw, R, 366 * 2.3 * (1 + collapse * 0.8), hgt, 366, 49)
        if pl_ is None: continue
        items.append((pl_[1], tr, pl_, j, arr, Cw))
    for depth, tr, pl_, j, arr, Cw in sorted(items, key=lambda v: -v[0]):
        a = clamp(arr * 1.5) * clamp(1 - j * 0.16)
        on_plane(c, pl_, lambda cc, tr=tr: draw_notice(cc, notice_text(tr), 0, 0), blur=min(8, j * 1.6), alpha=a)
        if j < 0.6 and arr < 1:
            pr, _ = cam.proj(Cw)
            for d_ in (0.0, 0.12):
                kk = lin(arr, 0.3 + d_, 1.0)
                if 0 < kk < 1:
                    rp = skia.Paint(AntiAlias=True, Style=skia.Paint.kStroke_Style, StrokeWidth=1.5, Color=col((120, 200, 255), 0.5 * (1 - kk)))
                    c.save(); c.translate(float(pr[0]), float(pr[1])); c.scale(1, 0.32)
                    c.drawCircle(0, 0, mix(300, 820, eo_cubic(kk)), rp); c.restore()
    # collapse into light line
    lk = env(t, 34.95, 35.15, 35.15, 35.6, eo_cubic, eo_cubic)
    if lk > 0:
        pr, _ = cam.proj(np.array([0, 0, 200.0]))
        lp = skia.Paint(AntiAlias=True); lp.setBlendMode(skia.BlendMode.kPlus)
        lp.setShader(skia.GradientShader.MakeLinear([skia.Point(0, 0), skia.Point(W, 0)], [col(ICE, 0), col(WHITE, lk), col(ICE, 0)], [0, .5, 1]))
        c.drawRect(skia.Rect(0, pr[1] - 2, W, pr[1] + 2), lp); glow(c, 960, float(pr[1]), 500, ICE, 0.35 * lk)

# ---- shot 7: video wall
def wall_items(t):
    s = state_at(LED, P(t))
    return [("C", ad.CARD, 0.58, 4.0, None), ("A", ad.A_FULL, 720 / 1352, 6.0, None), ("B", ad.B_FULL, 1080 / 1942, 9.0, None),
            ("UI", None, 390 / 694, 0, s), ("D", ad.CARD, 0.58, 0.0, None)]
def shot7(c, t):
    u = lin(t, 35.25, 40.0)
    cam = Cam.look((mix(-120, 140, eio_cubic(u)), -160, mix(-700, -1650, eo_cubic(lin(t, 35.25, 39.0)))), (0, 20, 600))
    draw_space(c, t, cam, accent=(120, 170, 255), grid=0.7, floor_y=430)
    implode = ei_cubic(lin(t, 38.75, 39.95))
    Rr = 1700; zc = 500
    items = []
    for k_, (clip, src, asp, off, s) in enumerate(wall_items(t)):
        a = (k_ - 2) * 0.33
        hgt = 760
        unf = eo_expo(lin(t, 35.3 + abs(k_ - 2) * 0.12, 36.1 + abs(k_ - 2) * 0.12))
        Cw = np.array([Rr * math.sin(a), 0, zc - Rr + Rr * math.cos(a)]) if False else np.array([Rr * math.sin(a), 0, zc + Rr * (1 - math.cos(a)) * -1])
        Cw = Cw + (np.array([0, 0, zc + 300]) - Cw) * implode
        R = rot(a * (1 - implode) + implode * (k_ - 2) * 1.2, 0, implode * (k_ - 2) * 0.6)
        sc = (1 - implode * 0.97)
        w_, h_ = hgt * asp * sc, hgt * unf * sc
        if h_ < 1: continue
        sw, sh = (390, 694) if clip == "UI" else (src[2] - src[0], src[3] - src[1])
        pl_ = plane(cam, Cw, R, w_, h_, sw, sh)
        if pl_ is None: continue
        items.append((pl_[1], clip, src, off, s, pl_, Cw, R, w_, h_, sw, sh))
    for depth, clip, src, off, s, pl_, Cw, R, w_, h_, sw, sh in sorted(items, key=lambda v: -v[0]):
        def fn(cc, clip=clip, src=src, off=off, s=s, sw=sw, sh=sh, refl=False):
            rad = 0.07 * sw
            cc.save(); cc.clipRRect(rrect(0, 0, sw, sh, rad), True)
            if clip == "UI": draw_ui(cc, s)
            else:
                img = clip_frame(clip, (t - 35.25) + off)
                cc.drawImageRect(img, skia.Rect(*src), skia.Rect(0, 0, sw, sh), SAMP, skia.Paint(AntiAlias=True))
            cc.restore()
            stp = skia.Paint(AntiAlias=True, Style=skia.Paint.kStroke_Style, StrokeWidth=2.0 * sw / 400)
            stp.setShader(skia.GradientShader.MakeLinear([skia.Point(0, 0), skia.Point(sw, sh)], [col(WHITE, .5), col(WHITE, .05), col(ICE, .3)], [0, .55, 1]))
            cc.drawRRect(rrect(0, 0, sw, sh, rad), stp)
        # reflection
        Cr = np.array([Cw[0], 2 * 430 - Cw[1] - (430 - (Cw[1] + h_ / 2)) * 0, Cw[2]])
        Cr = np.array([Cw[0], 430 + (430 - Cw[1]), Cw[2]])
        plr = plane(cam, Cr, R, w_, h_, sw, sh)
        if plr is not None:
            def rfn(cc, fn=fn, sh=sh):
                cc.translate(0, sh); cc.scale(1, -1); fn(cc)
            on_plane(c, plr, rfn, blur=6, alpha=0.12 * (1 - implode))
        pr, _ = cam.proj(Cw)
        glow(c, float(pr[0]), float(pr[1]), 380, ICE, 0.06 * (1 - implode))
        on_plane(c, pl_, fn)
    # implosion core + flash
    ik = env(t, 39.2, 39.9, 39.95, 40.0, ei_cubic, eo_cubic)
    pr, _ = cam.proj(np.array([0, 0, zc + 300.0]))
    glow(c, float(pr[0]), float(pr[1]), 600 * ik + 50, (120, 200, 255), 0.6 * ik)
    flash(c, env(t, 39.8, 40.0, 40.0, 40.0, ei_cubic, eo_cubic) * 1.0, (200, 230, 255))

# ---- shot 8+9: Telegram + CTA
def tg_state(t):
    sp = eo_expo(lin(t, 40.0, 41.5))
    mv = eio_quint(lin(t, 44.5, 45.5))
    yaw = mix(3 * 2 * math.pi + 0.6, 0, sp) + 0.12 * math.sin(t * 1.1) * sp
    pos = np.array([mix(0, 160 * 1.0, mv), mix(-90, -210, mv), mix(0, 0, mv)])
    size = mix(330, 170, mv)
    return pos, rot(yaw, 0.05 * math.sin(t * 0.8), 0), size

def shot8(c, t):
    cam = Cam.look((0, -300, -1400 + 60 * (t - 40)), (0, 0, 500))
    cta = lin(t, 44.5, 45.6)
    draw_space(c, t, cam, accent=TG, grid=mix(0.85, 0.35, cta), floor_y=300, nebula=1.2)
    imp = env(t, 40.0, 40.05, 40.05, 41.8, eo_cubic, eo_cubic)
    pos, R, size = tg_state(t)
    pr, _ = OBJ.proj(pos)
    rays(c, float(pr[0]), float(pr[1]), (0.07 + 0.12 * imp) * (1 - cta * 0.6), (60, 170, 255), t)
    for d_, a_ in ((0, 0.9), (0.2, 0.5)):
        k = lin(t, 40.0 + d_, 41.7 + d_)
        if 0 < k < 1: floor_ring(c, cam, 0, 500, mix(80, 2800, eo_cubic(k)), 300, a_ * (1 - k), (90, 190, 255), 2.4)
    # huge outlined word
    k = env(t, 40.5, 41.3, 44.3, 44.9)
    outline_text(c, "TELEGRAM", 960 - (t - 40) * 30, 380, "InterDisplay-Black", 300, (90, 190, 255), 0.22 * k, 2.0, "center", fill_a=0.08)
    glow(c, float(pr[0]), float(pr[1]), size * 2.2, TG, 0.30 + 0.25 * imp)
    # paper-plane light trail
    tk = lin(t, 41.0, 43.4)
    if 0 < tk < 1:
        us = np.linspace(max(0, tk - 0.35), tk, 60)
        def path_pt(u):
            a = u * 2.6 * math.pi - 1.2; r = 520 - 120 * u
            return np.array([r * math.cos(a) + 900 * max(0, u - 0.8) * 5, -90 - 260 * math.sin(u * math.pi) + 60 * math.sin(a), r * math.sin(a) * 0.9])
        pts = np.array([path_pt(u) for u in us]); scr, z = OBJ.proj(pts)
        if (z > 60).all():
            for i in range(len(scr) - 1):
                a = (i / len(scr)) ** 1.5 * (1 - lin(tk, 0.85, 1))
                p = skia.Paint(AntiAlias=True, Style=skia.Paint.kStroke_Style, StrokeWidth=1 + 5 * i / len(scr), Color=col((170, 225, 255), a))
                p.setStrokeCap(skia.Paint.kRound_Cap); p.setBlendMode(skia.BlendMode.kPlus)
                c.drawLine(float(scr[i][0]), float(scr[i][1]), float(scr[i + 1][0]), float(scr[i + 1][1]), p)
            glow(c, float(scr[-1][0]), float(scr[-1][1]), 40, WHITE, 0.9 * (1 - lin(tk, 0.85, 1)))
    extruded(c, OBJ, pos, R, size, size, 60 * size / 330, size / 2, face_tg, side0=(10, 60, 110), side1=(45, 150, 220), sheen=lin(t, 41.6, 42.4), rim=(150, 220, 255))
    # GrokBot tile joins for the CTA
    gk = eo_expo(lin(t, 44.8, 45.8))
    if gk > 0:
        gpos = np.array([mix(-700, -160, gk), -210, 0]); gR = rot(mix(-1.6, 0, gk) + 0.12 * math.sin(t * 1.1 + 1.5), 0.05 * math.sin(t * 0.8 + 1), 0)
        glow(c, 800, 330, 380, ICE, 0.16 * gk)
        extruded(c, OBJ, gpos, gR, 170, 170, 34, 170 * 0.225, face_logo, sheen=lin(t, 47.6, 48.3), alpha=clamp(gk * 1.5))
        if t > 45.7:
            ck = eo_expo(lin(t, 45.7, 46.4))
            x0, x1 = 885, 1035
            lp = skia.Paint(AntiAlias=True)
            lp.setShader(skia.GradientShader.MakeLinear([skia.Point(x0, 0), skia.Point(x1, 0)], [col(ICE, 0), col(ICE, .6 * ck), col(TG, 0)], [0, .5, 1]))
            c.drawRect(skia.Rect(mix(960, x0, ck), 329.3, mix(960, x1, ck), 330.7), lp)
            ph = ((t - 45.7) / 1.4) % 1.0; glow(c, mix(x0, x1, eio_cubic(ph)), 330, 16, WHITE, 0.8 * math.sin(ph * math.pi) * ck)
    # CTA copy
    if t > 45.6:
        reveal_words(c, "Join GrokBot on Telegram", 960, 548, "InterDisplay-SemiBold", 66, 45.9, t, WHITE, 1, "center", stagger=0.07)
        pk = eo_expo(lin(t, 46.3, 47.1))
        if pk > 0:
            tx_ = "t.me/grokbotting"; fs = 84; tw_ = text_w(tx_, "InterDisplay-SemiBold", fs)
            PW, PHh = tw_ + 150, 140; w_ = mix(PHh, PW, pk); x = 960 - w_ / 2; y = 680 - PHh / 2
            glow(c, 960, 680, 640, TG, 0.18 * pk + 0.12 * env(t, 49.9, 50.05, 50.05, 51.2))
            sp = skia.Paint(AntiAlias=True, Color=col((0, 0, 0), 0.5 * pk)); sp.setMaskFilter(skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, 26))
            c.drawRRect(rrect(x, y + 16, w_, PHh, PHh / 2), sp)
            rr = rrect(x, y, w_, PHh, PHh / 2)
            fp = skia.Paint(AntiAlias=True)
            fp.setShader(skia.GradientShader.MakeLinear([skia.Point(x, y), skia.Point(x + w_, y + PHh)], [col((20, 74, 112), .88 * pk), col((10, 30, 52), .92 * pk)]))
            c.drawRRect(rr, fp)
            st = skia.Paint(AntiAlias=True, Style=skia.Paint.kStroke_Style, StrokeWidth=2)
            st.setShader(skia.GradientShader.MakeLinear([skia.Point(x, y), skia.Point(x + w_, y + PHh)], [col((150, 220, 255), .95 * pk), col(TG, .35 * pk), col((150, 220, 255), .7 * pk)], [0, .5, 1]))
            c.drawRRect(rr, st)
            c.save(); c.clipRRect(rr, True)
            tk2 = eo_expo(lin(t, 46.6, 47.4))
            draw_text(c, tx_, 960, 680 + fs * 0.36, "InterDisplay-SemiBold", fs, WHITE, tk2, "center", tracking=0.0 if tk2 > 0.995 else mix(0.14, 0, tk2))
            for g0 in (47.5, 49.95):
                g = lin(t, g0, g0 + 0.8)
                if 0 < g < 1:
                    gx = mix(x - 200, x + w_ + 200, eio_cubic(g))
                    gp = skia.Paint(AntiAlias=True); gp.setBlendMode(skia.BlendMode.kPlus)
                    gp.setShader(skia.GradientShader.MakeLinear([skia.Point(gx - 120, y), skia.Point(gx + 120, y + PHh)], [col(WHITE, 0), col(WHITE, .3), col(WHITE, 0)]))
                    c.drawRect(skia.Rect(x, y, x + w_, y + PHh), gp)
            c.restore()
        k = eo_cubic(lin(t, 47.2, 48.0))
        draw_text(c, "Open the link in Telegram to get started", 960, 806 + (1 - k) * 12, "InterDisplay-Regular", 28, (170, 180, 196), k, "center")
        k = eo_cubic(lin(t, 46.8, 47.8))
        draw_text(c, "Illustrative data shown. Not financial advice. Crypto trading carries risk.", 960, 1048, "Inter-Regular", 16, GRAY, 0.8 * k, "center")
    flash(c, env(t, 40.0, 40.0, 40.0, 40.7, eo_cubic, eo_cubic) * 0.9, (200, 230, 255))

SHOTS = [(0, 4.0, shot1), (4.0, 8.0, shot2), (8.0, 14.1, shot3), (14.1, 24.05, shot4), (24.05, 30.2, shot5),
         (30.2, 35.25, shot6), (35.25, 40.0, shot7), (40.0, 52.01, shot8)]

# ------------------------------------------------------------------ post
IMPACTS = [4.0, 8.0, 14.1, 24.05, 30.2, 35.25, 40.0, 50.0]
def shake_at(t):
    a = 0.0
    for ti, amp in ((4.0, 9), (14.1, 6), (24.05, 7), (40.0, 12), (50.0, 5)):
        if ti <= t < ti + 0.5: a += amp * math.exp(-(t - ti) * 9)
    return a * math.sin(t * 91), a * math.cos(t * 77)

def bloom(surf, c, amount=0.55, thresh=0.55):
    img = surf.makeImageSnapshot()
    k = 1 / (1 - thresh)
    cm = [k, 0, 0, 0, -thresh * k, 0, k, 0, 0, -thresh * k, 0, 0, k, 0, -thresh * k, 0, 0, 0, 1, 0]
    out = None
    for div, sig, amt in ((4, 4, amount * 0.6), (8, 6, amount)):
        sw, sh = W // div, H // div
        sm = skia.Surface(sw, sh); scv = sm.getCanvas(); scv.clear(skia.ColorBLACK)
        p = skia.Paint(); p.setColorFilter(skia.ColorFilters.Matrix(cm)); p.setImageFilter(skia.ImageFilters.Blur(sig, sig, skia.TileMode.kClamp))
        scv.drawImageRect(img, skia.Rect(0, 0, sw, sh), skia.SamplingOptions(skia.FilterMode.kLinear, skia.MipmapMode.kLinear), p)
        simg = sm.makeImageSnapshot()
        bp = skia.Paint(); bp.setBlendMode(skia.BlendMode.kPlus); bp.setAlphaf(amt)
        c.drawImageRect(simg, skia.Rect(0, 0, W, H), skia.SamplingOptions(skia.FilterMode.kLinear), bp)

def render_into(arr, t):
    surf = skia.Surface(arr); c = surf.getCanvas()
    sx, sy = shake_at(t)
    c.save(); c.translate(sx, sy)
    for a, b, fn in SHOTS:
        if a <= t < b: fn(c, t)
    c.restore()
    bloom(surf, c)
    vignette(c)
    hud(c, t)
    subtitles(c, t)
    fin = 1 - eo_cubic(lin(t, 0.0, 0.25)); fout = eio_cubic(lin(t, 51.3, 52.0))
    fa = max(fin * 0, fout)
    if fa > 0: c.drawRect(skia.Rect(0, 0, W, H), skia.Paint(Color=col((0, 0, 0), fa)))
    surf.flushAndSubmit(); del surf

def chroma_amt(t):
    a = 0.0
    for ti in IMPACTS:
        if ti <= t < ti + 0.4: a = max(a, 7 * math.exp(-(t - ti) * 10))
    if 23.5 <= t < 24.05: a = max(a, 6 * lin(t, 23.5, 24.05))
    return a

GLITCH = [(24.0, 24.25), (30.05, 30.35), (3.9, 4.05)]
def post(rgb, t, idx):
    a = chroma_amt(t)
    if a > 0.5:
        n = int(round(a)); out = rgb.copy()
        out[:, n:, 0] = rgb[:, :-n, 0]; out[:, :-n, 2] = rgb[:, n:, 2]; rgb = out
    for g0, g1 in GLITCH:
        if g0 <= t < g1:
            r = np.random.default_rng(idx)
            for _ in range(10):
                y0 = int(r.integers(0, H - 40)); hh = int(r.integers(6, 60)); dx = int(r.integers(-80, 80))
                rgb[y0:y0 + hh] = np.roll(rgb[y0:y0 + hh], dx, axis=1)
                if r.random() < 0.4: rgb[y0:y0 + hh, :, int(r.integers(0, 3))] //= 2
    return rgb

MB = [((3.0, 4.05), 8), ((4.0, 4.9), 6), ((7.0, 8.1), 8), ((8.6, 10.4), 3), ((12.4, 14.15), 8), ((14.1, 15.1), 6),
      ((16.45, 16.95), 6), ((18.95, 19.45), 6), ((21.45, 21.95), 6), ((23.4, 24.1), 10), ((24.05, 30.2), 6),
      ((30.4, 34.6), 3), ((34.55, 35.6), 6), ((35.25, 36.6), 4), ((38.7, 40.05), 10), ((40.0, 41.6), 8), ((44.5, 45.9), 6)]

_GR = None
def frame(t, idx=0):
    global _GR
    if _GR is None:
        r = np.random.default_rng(9); _GR = [r.integers(-3, 4, size=(H, W, 1), dtype=np.int16) for _ in range(6)]
    arr = np.zeros((H, W, 4), np.uint8)
    n = 1
    for (a, b), k in MB:
        if a <= t <= b: n = max(n, k)
    if n == 1:
        render_into(arr, t); acc = arr[..., :3].astype(np.int16)
    else:
        sh = 0.5 / FPS; accf = np.zeros((H, W, 3), np.float32)
        for k in range(n):
            arr[:] = 0; ts = t - sh / 2 + sh * (k + 0.5) / n; ad._DT[0] = ts - t
            render_into(arr, ts); accf += arr[..., :3]
        ad._DT[0] = 0.0; acc = (accf / n + 0.5).astype(np.int16)
    acc += _GR[idx % 6]
    rgb = np.clip(acc, 0, 255).astype(np.uint8)
    return post(rgb, t, idx)

if __name__ == "__main__":
    import time
    from PIL import Image
    out = sys.argv[1]
    for tt in [float(x) for x in sys.argv[2:]]:
        t0 = time.time(); f = frame(tt, int(tt * FPS))
        Image.fromarray(f).save(os.path.join(out, "r_%05.2f.png" % tt)); print(tt, "%.2fs" % (time.time() - t0), flush=True)
