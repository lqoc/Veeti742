"""GrokBot promo — deterministic motion-graphics renderer (Skia). frame(t) -> RGBA uint8 (1080x1920)."""
import math, os, functools, io
import numpy as np, skia
from PIL import Image, ImageFont

W, H, FPS, DUR = 1920, 1080, 60, 42.0
HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "src")
UP = "/root/.claude/uploads/96587bbd-ccc0-5b0a-9133-58e2d44c6ac5"
FD = "/usr/share/fonts/opentype/inter/"

# ------------------------------------------------------------------ easing
def clamp(x, a=0.0, b=1.0): return a if x < a else b if x > b else x
def lin(t, a, b): return clamp((t - a) / (b - a)) if b > a else float(t >= a)
def mix(a, b, k): return a + (b - a) * k
def eo_expo(x): return 1.0 if x >= 1 else 1 - 2 ** (-10 * x)
def eo_cubic(x): return 1 - (1 - x) ** 3
def eo_quart(x): return 1 - (1 - x) ** 4
def ei_cubic(x): return x ** 3
def eio_cubic(x): return 4 * x ** 3 if x < .5 else 1 - (-2 * x + 2) ** 3 / 2
def eio_quint(x): return 16 * x ** 5 if x < .5 else 1 - (-2 * x + 2) ** 5 / 2
def eio_expo(x):
    if x <= 0: return 0.0
    if x >= 1: return 1.0
    return 2 ** (20 * x - 10) / 2 if x < .5 else (2 - 2 ** (-20 * x + 10)) / 2
def env(t, a, b, c, d, ein=eo_cubic, eout=eio_cubic):
    """0 before a, rises a->b, holds, falls c->d."""
    return ein(lin(t, a, b)) * (1 - eout(lin(t, c, d)))

# ------------------------------------------------------------------ palette
BG0 = (4, 6, 10)
ICE = (158, 216, 255)
WHITE = (244, 247, 251)
GRAY = (138, 148, 166)
TG = (42, 171, 238)
AGENTS = [("Scanner", (247, 166, 35)), ("Analyst", (47, 128, 237)),
          ("Executor", (61, 214, 120)), ("Risk", (178, 102, 255))]
def col(rgb, a=1.0): return skia.Color(int(rgb[0]), int(rgb[1]), int(rgb[2]), int(clamp(a) * 255))
def paint(rgb=WHITE, a=1.0, **kw):
    p = skia.Paint(AntiAlias=True, Color=col(rgb, a))
    for k, v in kw.items(): setattr(p, k, v) if False else None
    return p

SAMP = skia.SamplingOptions(skia.FilterMode.kLinear, skia.MipmapMode.kLinear)

# ------------------------------------------------------------------ fonts
TFS = {}
def typeface(name):
    if name not in TFS: TFS[name] = skia.Typeface.MakeFromFile(FD + name + ".otf")
    return TFS[name]
@functools.lru_cache(maxsize=256)
def font(name, size):
    f = skia.Font(typeface(name), size); f.setSubpixel(True); f.setEdging(skia.Font.Edging.kSubpixelAntiAlias if False else skia.Font.Edging.kAntiAlias)
    f.setHinting(skia.FontHinting.kNone); return f
@functools.lru_cache(maxsize=4096)
def pil_font(name, size): return ImageFont.truetype(FD + name + ".otf", int(round(size * 8)), layout_engine=ImageFont.Layout.RAQM)
@functools.lru_cache(maxsize=8192)
def text_w(s, name, size):  # shaped advance width
    return pil_font(name, size).getlength(s) / 8.0
@functools.lru_cache(maxsize=1024)
def blob(s, name, size): return skia.TextBlob.MakeFromShapedText(s, font(name, size))

def draw_text(c, s, x, y, name, size, rgb=WHITE, a=1.0, align="left", tracking=0.0, shader=None, blur=0.0):
    """tracking in em. Returns drawn width."""
    if a <= 0.002: return 0
    p = skia.Paint(AntiAlias=True, Color=col(rgb, a))
    if shader is not None: p.setShader(shader); p.setAlphaf(clamp(a))
    if blur > 0.3: p.setMaskFilter(skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, blur))
    if abs(tracking) < 1e-4:
        w = text_w(s, name, size)
        x0 = x - w / 2 if align == "center" else x - w if align == "right" else x
        c.drawTextBlob(blob(s, name, size), x0, y + font(name, size).getMetrics().fAscent, p)
        return w
    f = font(name, size)
    gl = f.textToGlyphs(s); ws = f.getWidths(gl)
    tr = tracking * size
    w = sum(ws) + tr * (len(ws) - 1)
    x0 = x - w / 2 if align == "center" else x - w if align == "right" else x
    for ch, gw in zip(s, ws):
        c.drawString(ch, x0, y, f, p); x0 += gw + tr
    return w

def reveal_words(c, s, x, y, name, size, t0, t, rgb=WHITE, a=1.0, align="left", stagger=0.07, dur=0.75,
                 shader=None, rise=1.05):
    """Masked per-word rise reveal (each word slides up from below a clip line)."""
    words = s.split(" ")
    total = text_w(s, name, size)
    x0 = x - total / 2 if align == "center" else x - total if align == "right" else x
    asc = size * 1.0; desc = size * 0.32
    c.save()
    c.clipRect(skia.Rect(x0 - size, y - asc - size * 0.25, x0 + total + size, y + desc))
    for i, wd in enumerate(words):
        pre = " ".join(words[:i]) + (" " if i else "")
        wx = x0 + text_w(pre, name, size)
        k = eo_expo(lin(t, t0 + i * stagger, t0 + i * stagger + dur))
        if k <= 0: continue
        dy = (1 - k) * size * rise
        draw_text(c, wd, wx, y + dy, name, size, rgb, a * clamp(k * 1.4), "left", shader=shader)
    c.restore()

# ------------------------------------------------------------------ images
def sk_from_pil(im):
    b = io.BytesIO(); im.save(b, "PNG")
    return skia.Image.MakeFromEncoded(skia.Data.MakeWithCopy(b.getvalue())).makeRasterImage().withDefaultMipmaps()
LOGO = sk_from_pil(Image.open(UP + "/87778fb1-image.webp").convert("RGBA"))
TGLOGO = sk_from_pil(Image.open(UP + "/8faa9910-image.png").convert("RGBA").resize((1024, 1024), Image.LANCZOS))

CLIPS = {"A": (60, 720, 1352), "B": (60, 1080, 1942), "C": (30, 720, 1280), "D": (30, 720, 1212)}
NFR = {k: len(os.listdir(os.path.join(SRC, k))) for k in CLIPS}
@functools.lru_cache(maxsize=24)
def _frame(clip, i):
    d = skia.Data.MakeFromFileName(os.path.join(SRC, clip, "%05d.jpg" % (i + 1)))
    return skia.Image.MakeFromEncoded(d).makeRasterImage().withDefaultMipmaps()
_DT = [0.0]  # sub-frame offset: footage is always sampled at the frame centre (no double exposure)
def clip_frame(clip, src_t):
    fps = CLIPS[clip][0]
    src_t -= _DT[0]
    i = int(clamp(math.floor(src_t * fps + 1e-6), 0, NFR[clip] - 1))
    return _frame(clip, i)

# ------------------------------------------------------------------ geometry
def proj_matrix(cx, cy, w, h, ry=0.0, rx=0.0, D=2600.0):
    """Matrix mapping local rect (0,0,w,h) to a 3D-rotated, perspective-projected quad centred at cx,cy."""
    pts = []
    cr, sr, cx_, sx_ = math.cos(ry), math.sin(ry), math.cos(rx), math.sin(rx)
    for x, y in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)):
        x1, z1 = x * cr, x * sr
        y2, z2 = y * cx_ - z1 * sx_, y * sx_ + z1 * cx_
        s = D / (D + z2)
        pts.append(skia.Point(cx + x1 * s, cy + y2 * s))
    m = skia.Matrix()
    m.setPolyToPoly([skia.Point(0, 0), skia.Point(w, 0), skia.Point(w, h), skia.Point(0, h)], pts)
    return m

def rrect(x, y, w, h, r): return skia.RRect.MakeRectXY(skia.Rect.MakeXYWH(x, y, w, h), r, r)

def draw_media(c, img, src, cx, cy, w, h, ry=0.0, rx=0.0, alpha=1.0, radius=0.0, bezel=0.0, blur=0.0,
               shadow=1.0, rim=1.0, glare=None, cover=False):
    """Draw image crop `src`(l,t,r,b) into a (w,h) screen panel, rounded, with device bezel/shadow. Returns matrix."""
    if alpha <= 0.003: return proj_matrix(cx, cy, w, h, ry, rx)
    m = proj_matrix(cx, cy, w, h, ry, rx)
    l, t_, r_, b_ = src
    if cover:  # crop src to panel aspect (no distortion)
        sw, sh = r_ - l, b_ - t_; a_p = w / h
        if sw / sh > a_p:
            nw = sh * a_p; l += (sw - nw) / 2; r_ = l + nw
        else:
            nh = sw / a_p; t_ += (sh - nh) / 2; b_ = t_ + nh
    c.save(); c.concat(m)
    pad = bezel * h * 0.018
    if shadow > 0:
        sp = skia.Paint(AntiAlias=True, Color=col((0, 0, 0), 0.62 * alpha * shadow))
        sp.setMaskFilter(skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, h * 0.045))
        c.drawRRect(rrect(-pad, -pad + h * 0.035, w + 2 * pad, h + 2 * pad, radius + pad), sp)
    if bezel > 0:
        body = rrect(-pad, -pad, w + 2 * pad, h + 2 * pad, radius + pad)
        bp = skia.Paint(AntiAlias=True)
        bp.setShader(skia.GradientShader.MakeLinear([skia.Point(0, -pad), skia.Point(w, h + pad)],
                                                    [col((30, 35, 46), alpha * bezel), col((10, 12, 17), alpha * bezel)]))
        c.drawRRect(body, bp)
        st = skia.Paint(AntiAlias=True, Style=skia.Paint.kStroke_Style, StrokeWidth=1.6)
        st.setShader(skia.GradientShader.MakeLinear([skia.Point(0, -pad), skia.Point(w * 0.6, h + pad)],
                                                    [col(WHITE, 0.42 * alpha * bezel), col(WHITE, 0.05 * alpha * bezel), col(ICE, 0.18 * alpha * bezel)],
                                                    [0, 0.55, 1]))
        c.drawRRect(body, st)
    scr = rrect(0, 0, w, h, radius)
    c.save(); c.clipRRect(scr, True)
    ip = skia.Paint(AntiAlias=True); ip.setAlphaf(clamp(alpha))
    if blur > 0.4: ip.setImageFilter(skia.ImageFilters.Blur(blur, blur, skia.TileMode.kClamp))
    c.drawImageRect(img, skia.Rect(l, t_, r_, b_), skia.Rect(0, 0, w, h), SAMP, ip)
    if glare is not None:  # moving diagonal sheen
        gx = mix(-w * 0.8, w * 1.8, glare)
        gp = skia.Paint(AntiAlias=True)
        gp.setShader(skia.GradientShader.MakeLinear([skia.Point(gx - w * 0.35, 0), skia.Point(gx + w * 0.35, h * 0.35)],
                                                    [col(WHITE, 0), col(WHITE, 0.07 * alpha), col(WHITE, 0)]))
        c.drawRect(skia.Rect(0, 0, w, h), gp)
    c.restore()
    if rim > 0:
        st = skia.Paint(AntiAlias=True, Style=skia.Paint.kStroke_Style, StrokeWidth=1.2)
        st.setShader(skia.GradientShader.MakeLinear([skia.Point(0, 0), skia.Point(w * 0.4, h)],
                                                    [col(WHITE, 0.28 * alpha * rim), col(WHITE, 0.04 * alpha * rim)]))
        c.drawRRect(scr, st)
    c.restore()
    return m

def map_pt(m, x, y):
    p = m.mapXY(x, y); return p.x(), p.y()

def glass(c, x, y, w, h, r, a=1.0, backdrop=True):
    if a <= 0.003: return
    sp = skia.Paint(AntiAlias=True, Color=col((0, 0, 0), 0.55 * a))
    sp.setMaskFilter(skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, 28))
    c.drawRRect(rrect(x, y + 18, w, h, r), sp)
    rr = rrect(x, y, w, h, r)
    if backdrop:
        c.save(); c.clipRRect(rr, True)
        b = skia.Rect.MakeXYWH(x, y, w, h); f = skia.ImageFilters.Blur(18, 18, skia.TileMode.kClamp)
        rec = skia.Canvas.SaveLayerRec(b, None, f, 0); c.saveLayer(rec); c.restore(); c.restore()
    fp = skia.Paint(AntiAlias=True)
    fp.setShader(skia.GradientShader.MakeLinear([skia.Point(x, y), skia.Point(x + w, y + h)],
                                                [col((26, 32, 44), 0.78 * a), col((12, 15, 22), 0.84 * a)]))
    c.drawRRect(rr, fp)
    st = skia.Paint(AntiAlias=True, Style=skia.Paint.kStroke_Style, StrokeWidth=1.2)
    st.setShader(skia.GradientShader.MakeLinear([skia.Point(x, y), skia.Point(x + w * 0.7, y + h)],
                                                [col(WHITE, 0.30 * a), col(WHITE, 0.05 * a), col(ICE, 0.16 * a)], [0, .6, 1]))
    c.drawRRect(rr, st)

def crop_panel(c, clip, src_t, src, x, y, scale, a=1.0, reveal=1.0, pad=12, r=22, glare=None):
    """Glass panel containing a live magnified crop of real footage. reveal: left->right mask."""
    if a <= 0.003 or reveal <= 0.001: return
    l, t_, r_, b_ = src
    w, h = (r_ - l) * scale, (b_ - t_) * scale
    W2, H2 = w + 2 * pad, h + 2 * pad
    c.save()
    c.clipRect(skia.Rect(x - 60, y - 60, x + (W2 + 60) * reveal, y + H2 + 80))
    glass(c, x, y, W2, H2, r, a)
    img = clip_frame(clip, src_t)
    draw_media(c, img, src, x + pad + w / 2, y + pad + h / 2, w, h, alpha=a, radius=r - pad * 0.6, shadow=0, rim=0.6, glare=glare)
    c.restore()
    return (x, y, W2, H2)

def connector(c, p0, p1, k, a=1.0, rgb=ICE):
    """Callout line from p0 (on device) to p1 (panel) drawn with progress k."""
    if k <= 0 or a <= 0: return
    x0, y0 = p0; x1, y1 = p1
    mx = mix(x0, x1, 0.5)
    path = skia.Path(); path.moveTo(x0, y0); path.cubicTo(mx, y0, mx, y1, x1, y1)
    meas = skia.PathMeasure(path, False); L = meas.getLength()
    seg = skia.Path(); meas.getSegment(0, L * k, seg, True)
    lp = skia.Paint(AntiAlias=True, Style=skia.Paint.kStroke_Style, StrokeWidth=1.4, Color=col(rgb, 0.55 * a))
    c.drawPath(seg, lp)
    dp = skia.Paint(AntiAlias=True, Color=col(rgb, 0.95 * a))
    c.drawCircle(x0, y0, 4.2, dp)
    gp = skia.Paint(AntiAlias=True, Color=col(rgb, 0.35 * a)); gp.setMaskFilter(skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, 6))
    c.drawCircle(x0, y0, 9, gp)
    if k >= 1:
        c.drawCircle(x1, y1, 3.2, dp)

def glow(c, x, y, r, rgb, a):
    if a <= 0.002: return
    p = skia.Paint(AntiAlias=True)
    p.setShader(skia.GradientShader.MakeRadial(skia.Point(x, y), r, [col(rgb, a), col(rgb, a * 0.35), col(rgb, 0)], [0, 0.35, 1]))
    c.drawCircle(x, y, r, p)

def draw_logo(c, img, cx, cy, size, a=1.0, round_frac=0.225, sheen=None, blur=0.0, clip_r=None, glow_a=0.0, glow_rgb=ICE, circle=False):
    if a <= 0.003: return
    if glow_a > 0: glow(c, cx, cy, size * 1.25, glow_rgb, glow_a * a)
    r = size * (0.5 if circle else round_frac)
    rr = rrect(cx - size / 2, cy - size / 2, size, size, r)
    if not circle:
        sp = skia.Paint(AntiAlias=True, Color=col((0, 0, 0), 0.6 * a))
        sp.setMaskFilter(skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, size * 0.08))
        c.drawRRect(rrect(cx - size / 2, cy - size / 2 + size * 0.06, size, size, r), sp)
    c.save()
    if clip_r is not None:
        cp = skia.Path(); cp.addCircle(cx, cy, clip_r); c.clipPath(cp, skia.ClipOp.kIntersect, True)
    c.clipRRect(rr, True)
    ip = skia.Paint(AntiAlias=True); ip.setAlphaf(clamp(a))
    if blur > 0.4: ip.setImageFilter(skia.ImageFilters.Blur(blur, blur, skia.TileMode.kClamp))
    c.drawImageRect(img, skia.Rect(0, 0, img.width(), img.height()), skia.Rect.MakeXYWH(cx - size / 2, cy - size / 2, size, size), SAMP, ip)
    if sheen is not None and 0 < sheen < 1:
        gx = mix(cx - size * 1.3, cx + size * 1.3, sheen)
        gp = skia.Paint(AntiAlias=True); gp.setBlendMode(skia.BlendMode.kPlus)
        gp.setShader(skia.GradientShader.MakeLinear([skia.Point(gx - size * 0.28, cy - size * 0.5), skia.Point(gx + size * 0.28, cy + size * 0.5)],
                                                    [col(WHITE, 0), col(WHITE, 0.42 * a), col(WHITE, 0)]))
        c.drawRect(skia.Rect.MakeXYWH(cx - size / 2, cy - size / 2, size, size), gp)
    c.restore()
    if not circle:
        st = skia.Paint(AntiAlias=True, Style=skia.Paint.kStroke_Style, StrokeWidth=max(1.0, size * 0.006))
        st.setShader(skia.GradientShader.MakeLinear([skia.Point(cx - size / 2, cy - size / 2), skia.Point(cx + size / 2, cy + size / 2)],
                                                    [col(WHITE, 0.35 * a), col(WHITE, 0.03 * a)]))
        c.drawRRect(rr, st)

# ------------------------------------------------------------------ background
_rng = np.random.default_rng(11)
NP = 240
P_X = _rng.uniform(0, W + 80, NP); P_Y = _rng.uniform(0, H + 80, NP)
P_D = _rng.uniform(0.15, 1.0, NP) ** 1.6
P_VX = _rng.normal(0, 5, NP); P_VY = -_rng.uniform(4, 16, NP)
P_PH = _rng.uniform(0, 6.283, NP); P_F = _rng.uniform(0.4, 1.6, NP)
NB = 11
B_X = _rng.uniform(0, W, NB); B_Y = _rng.uniform(0, H, NB); B_R = _rng.uniform(50, 150, NB)
B_PH = _rng.uniform(0, 6.283, NB); B_A = _rng.uniform(0.02, 0.05, NB)

def tg_mix(t):  # how much the environment is tinted Telegram-blue
    return eio_cubic(lin(t, 31.2, 32.4))

def draw_bg(c, t, camx=0.0, energy=1.0):
    c.clear(col(BG0))
    tb = tg_mix(t)
    # big soft light pools
    a1 = (34, 64, 96); a2 = (18, 40, 70); tgc = (14, 78, 128)
    c1 = tuple(mix(a1[i], tgc[i], tb) for i in range(3))
    glow(c, 960 + 380 * math.sin(t * 0.21) - camx * 0.05, 300 + 90 * math.cos(t * 0.17), 1150, c1, 0.42)
    glow(c, 360 + 200 * math.cos(t * 0.13) - camx * 0.03, 900, 900, a2, 0.38)
    glow(c, 1650 + 120 * math.sin(t * 0.19) - camx * 0.04, 820, 800, tuple(mix(a2[i], tgc[i], tb) for i in range(3)), 0.30)
    # horizon line light
    hp = skia.Paint(AntiAlias=True)
    hp.setShader(skia.GradientShader.MakeLinear([skia.Point(0, 0), skia.Point(W, 0)],
                                                [col(ICE, 0), col(tuple(mix(ICE[i], TG[i], tb) for i in range(3)), 0.07), col(ICE, 0)]))
    c.drawRect(skia.Rect(0, 770, W, 772), hp)
    # bokeh
    for i in range(NB):
        x = (B_X[i] + 9 * t * (1 + i % 3) - camx * 0.12) % (W + 400) - 200
        y = B_Y[i] + 30 * math.sin(t * 0.3 + B_PH[i])
        p = skia.Paint(AntiAlias=True, Color=col(tuple(mix(ICE[k], TG[k], tb) for k in range(3)), B_A[i] * (0.7 + 0.3 * math.sin(t * 0.7 + B_PH[i]))))
        p.setMaskFilter(skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, B_R[i] * 0.45))
        c.drawCircle(x, y, B_R[i], p)
    # particles (3D drift + parallax)
    xs = (P_X + P_VX * t - camx * (0.05 + 0.25 * P_D)) % (W + 80) - 40
    ys = (P_Y + P_VY * t * (0.4 + P_D)) % (H + 80) - 40
    tw = 0.55 + 0.45 * np.sin(t * P_F * 2.1 + P_PH)
    pc = tuple(mix(ICE[k], (170, 220, 255)[k], tb) for k in range(3))
    for i in range(NP):
        d = P_D[i]; a = (0.10 + 0.55 * d) * tw[i] * energy
        r = 0.6 + 2.0 * d
        p = skia.Paint(AntiAlias=True, Color=col(pc if i % 5 else WHITE, a))
        if d > 0.75:
            p.setMaskFilter(skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, 1.2 + 2 * d))
            r *= 1.4
        c.drawCircle(float(xs[i]), float(ys[i]), r, p)

def vignette(c):
    p = skia.Paint(AntiAlias=True)
    p.setShader(skia.GradientShader.MakeRadial(skia.Point(W / 2, H / 2), 1150, [col((0, 0, 0), 0), col((0, 0, 0), 0.0), col((0, 0, 0), 0.62)], [0, 0.55, 1]))
    c.drawRect(skia.Rect(0, 0, W, H), p)

def label(c, s, x, y, t0, t, a=1.0, align="left", rgb=ICE, size=19):
    k = eo_expo(lin(t, t0, t0 + 0.9))
    if k <= 0: return
    draw_text(c, s, x, y, "Inter-Medium", size, rgb, a * clamp(k * 1.3), align, tracking=mix(0.6, 0.3, k))

def line_bar(c, x, y, w, a):  # small accent rule
    p = skia.Paint(AntiAlias=True)
    p.setShader(skia.GradientShader.MakeLinear([skia.Point(x, y), skia.Point(x + w, y)], [col(ICE, 0.9 * a), col(ICE, 0)]))
    c.drawRect(skia.Rect(x, y, x + w, y + 2), p)

# ------------------------------------------------------------------ timeline constants
PH_H = 900.0; PH_W_A = PH_H * 720 / 1352; PH_W_B = PH_H * 1080 / 1942
PH_R_A = PH_W_A * 0.085; PH_R_B = PH_W_B * 0.085
A_FULL = (0, 0, 720, 1352); B_FULL = (0, 0, 1080, 1942); CARD = (60, 133, 660, 1167)
def srcA(t): return t - 7.0
def srcB(t): return t - 11.5 if t < 26 else t - 27.5 + 14.0
def deg(x): return x * math.pi / 180

# ------------------------------------------------------------------ scenes
def scene_open(c, t):
    """0 - 8.0: streak, logo reveal, wordmark, headline, morph into phone."""
    if t > 8.05: return
    # converging light streaks
    if 0.35 < t < 2.4:
        k = eio_cubic(lin(t, 0.35, 1.95)); fade = 1 - lin(t, 1.95, 2.35)
        for side in (-1, 1):
            hx = 960 + side * mix(1300, 0, k)
            L = mix(80, 520, eo_cubic(lin(t, 0.35, 1.5)))
            x0, x1 = (hx - L, hx) if side < 0 else (hx, hx + L)
            gp = skia.Paint(AntiAlias=True); gp.setBlendMode(skia.BlendMode.kPlus)
            cs = [col(ICE, 0), col(WHITE, 0.9 * fade)] if side < 0 else [col(WHITE, 0.9 * fade), col(ICE, 0)]
            gp.setShader(skia.GradientShader.MakeLinear([skia.Point(x0, 0), skia.Point(x1, 0)], cs))
            c.drawRect(skia.Rect(x0, 454, x1, 456.2), gp)
            gp2 = skia.Paint(AntiAlias=True); gp2.setBlendMode(skia.BlendMode.kPlus)
            gp2.setShader(skia.GradientShader.MakeLinear([skia.Point(x0, 0), skia.Point(x1, 0)], [col(ICE, 0), col(ICE, 0.35 * fade)] if side < 0 else [col(ICE, 0.35 * fade), col(ICE, 0)]))
            gp2.setMaskFilter(skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, 7))
            c.drawRect(skia.Rect(x0, 448, x1, 462), gp2)
            glow(c, hx, 455, 60, ICE, 0.35 * fade * k)
    # flash bloom at impact
    fl = env(t, 1.9, 2.0, 2.0, 2.9, eo_cubic, eo_cubic)
    if fl > 0:
        glow(c, 960, 455, 700, ICE, 0.22 * fl); glow(c, 960, 455, 240, WHITE, 0.30 * fl)
        hp = skia.Paint(AntiAlias=True); hp.setBlendMode(skia.BlendMode.kPlus)
        hp.setShader(skia.GradientShader.MakeLinear([skia.Point(0, 0), skia.Point(W, 0)], [col(ICE, 0), col(WHITE, 0.5 * fl), col(ICE, 0)]))
        hp.setMaskFilter(skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, 3))
        c.drawRect(skia.Rect(0, 453, W, 457), hp)
    # ---- logo state
    if t < 1.95: return
    rv = eo_expo(lin(t, 1.95, 2.9))
    sc = mix(1.16, 1.0, eo_expo(lin(t, 1.95, 3.4)))
    bl = 12 * (1 - eo_cubic(lin(t, 1.95, 2.55)))
    mv = eio_quint(lin(t, 4.15, 5.05))
    cx, cy = 960, mix(455, 322, mv)
    size = mix(250, 150, mv) * sc
    # morph into device
    k1 = eio_quint(lin(t, 6.55, 7.35)); k2 = eio_quint(lin(t, 6.85, 7.9))
    cx, cy = mix(cx, 960, k1), mix(cy, 540, k1)
    breathe = 0.5 + 0.5 * math.sin(t * 2.0)
    if k2 <= 0:
        draw_logo(c, LOGO, cx, cy, size, 1.0, sheen=lin(t, 2.65, 3.45), blur=bl, clip_r=rv * size * 0.75,
                  glow_a=(0.20 + 0.05 * breathe) * rv)
    else:
        w = mix(size, PH_W_A, k2); h = mix(size, PH_H, k2); r = mix(size * 0.225, PH_R_A, k2)
        glow(c, cx, cy, mix(size * 1.25, 900, k2), ICE, 0.22 * (1 - k2 * 0.6))
        xf = eio_cubic(lin(t, 7.0, 7.55))
        draw_media(c, LOGO, (0, 0, LOGO.width(), LOGO.height()), cx, cy, w, h, alpha=1 - xf * 0.999, radius=r,
                   bezel=0, shadow=1 - xf, rim=1, cover=True)
        if xf > 0:
            draw_media(c, clip_frame("A", srcA(t)), A_FULL, cx, cy, w, h, alpha=xf, radius=r,
                       bezel=lin(t, 7.35, 7.9), shadow=xf, rim=1, cover=True)
    # wordmark
    wk = eo_expo(lin(t, 2.9, 4.0)); wout = eio_cubic(lin(t, 4.0, 4.5))
    if wk > 0 and wout < 1:
        draw_text(c, "GROKBOT", 960, 690 - wout * 20, "InterDisplay-SemiBold", 50, WHITE, clamp(wk * 1.2) * (1 - wout), "center",
                  tracking=mix(0.95, 0.36, wk), blur=wout * 6)
        lw = 240 * eo_expo(lin(t, 3.3, 4.1)) * (1 - wout)
        lp = skia.Paint(AntiAlias=True)
        lp.setShader(skia.GradientShader.MakeLinear([skia.Point(960 - lw, 0), skia.Point(960 + lw, 0)], [col(ICE, 0), col(ICE, 0.55), col(ICE, 0)], [0, .5, 1]))
        c.drawRect(skia.Rect(960 - lw, 735, 960 + lw, 736.2), lp)
    # headline
    hout = eio_cubic(lin(t, 6.45, 7.0))
    if t > 4.5 and hout < 1:
        c.save(); c.translate(0, -hout * 40)
        a = 1 - hout
        reveal_words(c, "Meet GrokBot.", 960, 585, "InterDisplay-Bold", 128, 4.55, t, WHITE, a, "center", stagger=0.12, dur=0.9)
        k = eo_cubic(lin(t, 5.15, 6.0))
        draw_text(c, "Automated trading agents, live on your phone.", 960, 672 + (1 - k) * 18, "InterDisplay-Light", 40,
                  (196, 204, 218), a * k, "center", blur=(1 - k) * 4)
        c.restore()

def phoneA_state(t):
    k = eio_quint(lin(t, 8.0, 8.9)); out = 0
    return mix(960, 600, k), 540 + 6 * math.sin(t * 1.3) * k, deg(10) * k

def scene_dash(c, t, ox):
    """7.9 - 13.5: 01 Live dashboard (clip A)."""
    if t < 7.9 or t > 13.6: return
    c.save(); c.translate(-ox, 0)
    cx, cy, ry = phoneA_state(t)
    img = clip_frame("A", srcA(t))
    glow(c, cx, cy, 760, ICE, 0.10)
    m = draw_media(c, img, A_FULL, cx, cy, PH_W_A, PH_H, ry=ry, radius=PH_R_A, bezel=1.0, glare=lin(t, 8.3, 10.3))
    X = 1000
    label(c, "01 — LIVE DASHBOARD", X, 262, 8.55, t)
    line_bar(c, X, 286, 64 * eo_expo(lin(t, 8.7, 9.5)), 1)
    reveal_words(c, "Your whole desk.", X, 362, "InterDisplay-SemiBold", 66, 8.7, t)
    reveal_words(c, "One live view.", X, 438, "InterDisplay-SemiBold", 66, 8.85, t, rgb=(170, 182, 200))
    rk = eo_expo(lin(t, 9.05, 9.9))
    src = (20, 262, 700, 790)
    if rk > 0:
        box = crop_panel(c, "A", srcA(t), src, X, 488, 0.9, 1.0, rk, glare=lin(t, 9.7, 11.2))
        p0 = map_pt(m, PH_W_A * 0.985, PH_H * (380 / 1352))
        connector(c, p0, (X - 2, 488 + 260), eo_cubic(lin(t, 9.0, 9.6)), 1.0)
    c.restore()

def phoneB_state(t):
    # S3b: right side facing left; S3c: moves to left side facing right
    k = eio_quint(lin(t, 17.95, 18.75))
    x = mix(1390, 560, k); ry = mix(deg(-10), deg(10), k)
    return x, 540 + 6 * math.sin(t * 1.25 + 1), ry

def scene_agents_alerts(c, t, ox):
    """12.8 - 23.4: 02 agents + 03 alerts (clip B, continuous)."""
    if t < 12.8 or t > 23.4: return
    c.save(); c.translate(1920 - ox, 0)
    cx, cy, ry = phoneB_state(t)
    img = clip_frame("B", srcB(t))
    glow(c, cx, cy, 760, ICE, 0.10)
    m = draw_media(c, img, B_FULL, cx, cy, PH_W_B, PH_H, ry=ry, radius=PH_R_B, bezel=1.0, glare=lin(t, 13.4, 15.4))
    # ---- 02 agents (left column)
    if t < 18.4:
        X = 190; T0 = 13.45
        def out(i): return eio_cubic(lin(t, 17.7 + i * 0.05, 18.15 + i * 0.05))
        def grp(i):
            o = out(i); c.save(); c.translate(-70 * o, 0); return 1 - o
        a = grp(0); label(c, "02 — AGENTS", X, 262, T0, t, a); line_bar(c, X, 286, 64 * eo_expo(lin(t, T0 + .15, T0 + .9)), a); c.restore()
        a = grp(1); reveal_words(c, "Specialist agents.", X, 362, "InterDisplay-SemiBold", 66, T0 + 0.15, t, WHITE, a); c.restore()
        a = grp(2); reveal_words(c, "One desk.", X, 438, "InterDisplay-SemiBold", 66, T0 + 0.3, t, (170, 182, 200), a); c.restore()
        a = grp(3)
        rk = eo_expo(lin(t, T0 + 0.45, T0 + 1.3))
        src = (40, 1125, 1060, 1575)
        crop_panel(c, "B", srcB(t), src, X, 488, 0.645, a, rk, glare=lin(t, 14.6, 16.2))
        pr = (X + 1020 * 0.645 + 24, 488 + (450 * 0.645 + 24) / 2)
        p0 = map_pt(m, PH_W_B * 0.015, PH_H * (1350 / 1942))
        connector(c, p0, pr, eo_cubic(lin(t, T0 + 0.5, T0 + 1.1)), a)
        c.restore()
        a = grp(4)
        s = srcB(t); kExec = eio_cubic(lin(s, 5.15, 5.7))
        x = X; y = 868
        for i, (nm, rgb) in enumerate(AGENTS):
            ki = eo_expo(lin(t, T0 + 0.7 + i * 0.09, T0 + 1.5 + i * 0.09))
            act = (1 - kExec) if i < 2 else kExec
            aa = a * ki * mix(0.32, 1.0, act)
            dp = skia.Paint(AntiAlias=True, Color=col(rgb, aa))
            c.drawCircle(x + 7, y - 11, 6.5, dp)
            if act > 0.05: glow(c, x + 7, y - 11, 26, rgb, 0.5 * aa * act)
            wdt = draw_text(c, nm, x + 26, y + (1 - ki) * 16, "InterDisplay-Medium", 32, WHITE, aa)
            x += 26 + wdt + 40
        c.restore()
    # ---- 03 alerts (right column)
    if t > 18.3:
        X = 1000; T0 = 18.45
        def out2(i): return eio_cubic(lin(t, 22.75 + i * 0.03, 23.05 + i * 0.03))
        a = 1.0
        label(c, "03 — ALERTS", X, 372, T0, t, a); line_bar(c, X, 396, 64 * eo_expo(lin(t, T0 + .15, T0 + .9)), a)
        reveal_words(c, "Every exit,", X, 472, "InterDisplay-SemiBold", 66, T0 + 0.15, t)
        reveal_words(c, "straight to you.", X, 548, "InterDisplay-SemiBold", 66, T0 + 0.3, t, (170, 182, 200))
        s = srcB(t)
        vis = 0.0; slide = 0.0
        for (b0, b1) in ((6.8, 8.6), (10.1, 12.0)):
            v = eo_expo(lin(s, max(b0, srcB(18.85)), max(b0, srcB(18.85)) + 0.45)) * (1 - eio_cubic(lin(s, b1 - 0.3, b1 - 0.02)))
            if v > vis: vis = v; slide = 1 - eo_expo(lin(s, max(b0, srcB(18.85)), max(b0, srcB(18.85)) + 0.5))
        if vis > 0:
            y0 = 610 - 26 * slide
            crop_panel(c, "B", s, (26, 0, 1054, 170), X, y0, 0.8, vis, 1.0, pad=10, r=26)
            p0 = map_pt(m, PH_W_B * 0.985, PH_H * (85 / 1942))
            connector(c, p0, (X - 2, y0 + (170 * 0.8 + 20) / 2), vis, vis)
    c.restore()

def scene_positions(c, t):
    """22.8 - 28.4: 04 positions (clips C + D)."""
    if t < 22.8 or t > 28.5: return
    X = 190; T0 = 23.15
    label(c, "04 — POSITIONS", X, 372, T0, t)
    line_bar(c, X, 396, 64 * eo_expo(lin(t, T0 + .15, T0 + .9)), 1)
    reveal_words(c, "Every position,", X, 472, "InterDisplay-SemiBold", 66, T0 + 0.15, t)
    reveal_words(c, "tracked.", X, 548, "InterDisplay-SemiBold", 66, T0 + 0.3, t, (170, 182, 200))
    k = eo_cubic(lin(t, T0 + 0.6, T0 + 1.4))
    draw_text(c, "Entry, exit and PnL on every trade.", X, 616 + (1 - k) * 14, "InterDisplay-Regular", 28, GRAY, k)
    ch = 780; cw = ch * 600 / 1034; r = cw * 50 / 600
    for i, (clip, cx, ry, t1, st) in enumerate((("C", 1105, deg(7), T0 - 0.05, lambda tt: tt - 23.0 + 4.0),
                                                ("D", 1595, deg(-7), T0 + 0.4, lambda tt: tt - 23.6))):
        kk = eo_expo(lin(t, t1, t1 + 1.0))
        if kk <= 0: continue
        cy = 545 + (1 - kk) * 90 + 6 * math.sin(t * 1.2 + i * 1.7)
        glow(c, cx, cy, 600, ICE, 0.07 * kk)
        draw_media(c, clip_frame(clip, max(0, st(t))), CARD, cx, cy, cw, ch, ry=ry * mix(2.2, 1, kk), alpha=clamp(kk * 1.3),
                   radius=r + 1.5, blur=(1 - kk) * 8, glare=lin(t, t1 + 0.6, t1 + 2.6))

def scene_brand(c, t):
    """27.6 - 32.4: tagline over defocused devices, rack focus, push into Telegram badge."""
    if t < 27.55 or t > 32.4: return
    kin = eo_cubic(lin(t, 27.6, 28.6))
    rf = eio_cubic(lin(t, 29.65, 30.5))          # rack focus to B
    spread = eio_quint(lin(t, 29.65, 30.6))
    # side devices
    for clip, src, cx, ry, h, stf in (("A", A_FULL, 430, deg(22), 690, lambda tt: tt - 27.5 + 13.0),
                                      ("C", CARD, 1490, deg(-22), 690, lambda tt: 9.0)):
        a = 0.34 * kin * (1 - spread)
        if a <= 0.003: continue
        w = h * (src[2] - src[0]) / (src[3] - src[1])
        x = cx + (-1 if cx < 960 else 1) * (40 * (1 - kin) + 260 * spread)
        draw_media(c, clip_frame(clip, stf(t)), src, x, 560 + 5 * math.sin(t), w, h, ry=ry, alpha=a,
                   radius=w * 0.085, bezel=1 if clip == "A" else 0, blur=10, shadow=0.6)
    # B: centre device -> zoom into Telegram badge
    zk = eio_quint(lin(t, 30.55, 31.9))
    s0 = mix(760, 880, rf) / 1942
    ZEND = 300 / (50 * (880 / 1942))
    z = math.exp(mix(0, math.log(ZEND), zk))
    s = s0 * z
    ax, ay = 405, 928                                  # Telegram icon centre inside clip B
    anchor0 = (960 + (ax - 540) * s0, 540 + (ay - 971) * s0)
    anchor = (mix(anchor0[0], 960, zk), mix(anchor0[1], 420, zk))
    bcx = anchor[0] - (ax - 540) * s; bcy = anchor[1] - (ay - 971) * s
    aB = mix(0.34 * kin, 1.0, rf) * (1 - lin(z, 4.0, 9.0))
    if aB > 0.003:
        draw_media(c, clip_frame("B", srcB(t)), B_FULL, bcx, bcy, 1080 * s, 1942 * s, alpha=aB, radius=1080 * s * 0.085,
                   bezel=1.0 * (1 - lin(z, 1.5, 3)), blur=10 * (1 - rf), glare=lin(t, 30.0, 31.0) if rf > 0.5 else None)
    # Telegram logo takes over from the in-app icon
    la = lin(z, 2.3, 4.2)
    if la > 0:
        sz = 50 * s
        draw_logo(c, TGLOGO, anchor[0], anchor[1], sz, la, circle=True, glow_a=0.35 * la, glow_rgb=TG)
    # tagline
    if t < 30.2:
        dark = kin * (1 - lin(t, 29.4, 30.0))
        glow(c, 960, 540, 900, (0, 0, 0), 0.55 * dark)
        o = eio_cubic(lin(t, 29.45, 29.95))
        c.save(); c.translate(0, -36 * o)
        a = 1 - o
        lk = eo_expo(lin(t, 27.75, 28.6))
        draw_logo(c, LOGO, 960, 356, 118 * mix(0.85, 1, lk), a * lk, glow_a=0.25, sheen=lin(t, 28.5, 29.2))
        reveal_words(c, "Your trading desk.", 960, 560, "InterDisplay-Bold", 96, 27.95, t, WHITE, a, "center", stagger=0.09)
        sh = skia.GradientShader.MakeLinear([skia.Point(700, 0), skia.Point(1220, 0)], [col(ICE), col(WHITE), col((120, 200, 255))])
        reveal_words(c, "Reimagined.", 960, 672, "InterDisplay-Light", 96, 28.25, t, WHITE, a, "center", shader=sh)
        c.restore()

def scene_telegram(c, t):
    """31.9 - 36.4: Telegram logo hero + 'Access GrokBot on Telegram'."""
    if t < 31.9 or t > 36.4: return
    mv = eio_quint(lin(t, 35.35, 36.2))
    fl = 6 * math.sin((t - 32) * 1.6) * (1 - mv)
    cx, cy, sz = mix(960, 1120, mv), mix(420 + fl, 330, mv), mix(300, 170, mv)
    pulse = env(t, 31.95, 32.05, 32.05, 33.2, eo_cubic, eo_cubic)
    glow(c, cx, cy, mix(620, 420, mv), TG, 0.22 + 0.25 * pulse)
    # shockwave rings
    for d, amp in ((0.0, 0.55), (0.16, 0.3)):
        k = lin(t, 32.0 + d, 33.1 + d)
        if 0 < k < 1:
            rr = mix(150, 620, eo_cubic(k))
            rp = skia.Paint(AntiAlias=True, Style=skia.Paint.kStroke_Style, StrokeWidth=mix(3, 0.6, k), Color=col((120, 205, 255), amp * (1 - k)))
            c.drawCircle(960, 420, rr, rp)
    # slow orbit dashes
    oa = 0.18 * lin(t, 32.2, 33.0) * (1 - mv)
    if oa > 0:
        op = skia.Paint(AntiAlias=True, Style=skia.Paint.kStroke_Style, StrokeWidth=1.2, Color=col(ICE, oa))
        op.setPathEffect(skia.DashPathEffect.Make([3, 11], (t * 18) % 14))
        c.drawCircle(cx, cy, sz * 0.78, op)
    if t < 36.2: draw_logo(c, TGLOGO, cx, cy, sz, 1.0, circle=True, sheen=lin(t, 32.9, 33.6))
    o = eio_cubic(lin(t, 35.2, 35.6))
    if o < 1:
        c.save(); c.translate(0, -30 * o)
        label(c, "ACCESS GROKBOT ON", 960, 662, 32.35, t, 1 - o, "center", size=22)
        reveal_words(c, "Telegram", 960, 790, "InterDisplay-Bold", 120, 32.55, t, WHITE, 1 - o, "center")
        c.restore()

def scene_cta(c, t):
    """35.5 - 42: final call to action."""
    if t < 35.5: return
    # logos pair (Telegram logo continues from scene_telegram until 36.2)
    gk = eo_expo(lin(t, 35.65, 36.5))
    draw_logo(c, LOGO, mix(730, 800, gk), 330, 170 * mix(0.88, 1, gk), clamp(gk * 1.3), glow_a=0.18, sheen=lin(t, 37.55, 38.3))
    if t >= 36.2:
        draw_logo(c, TGLOGO, 1120, 330, 170, 1.0, circle=True, sheen=lin(t, 37.7, 38.45), glow_a=0.16, glow_rgb=TG)
    # connector with travelling pulse
    ck = eo_expo(lin(t, 36.1, 36.8))
    if ck > 0:
        x0, x1 = 905, 1015
        lp = skia.Paint(AntiAlias=True)
        lp.setShader(skia.GradientShader.MakeLinear([skia.Point(x0, 0), skia.Point(x1, 0)], [col(ICE, 0.0), col(ICE, 0.6 * ck), col(TG, 0.0)], [0, .5, 1]))
        c.drawRect(skia.Rect(mix(960, x0, ck), 329.2, mix(960, x1, ck), 330.8), lp)
        ph = ((t - 36.1) / 1.4) % 1.0
        px = mix(x0, x1, eio_cubic(ph)); pa = math.sin(ph * math.pi) * ck
        glow(c, px, 330, 16, WHITE, 0.8 * pa)
    reveal_words(c, "Join GrokBot on Telegram", 960, 548, "InterDisplay-SemiBold", 66, 36.2, t, WHITE, 1, "center", stagger=0.07)
    # link pill
    pk = eo_expo(lin(t, 36.5, 37.3))
    if pk > 0:
        txt = "t.me/grokbotting"; fs = 84
        tw_ = text_w(txt, "InterDisplay-SemiBold", fs)
        PW = tw_ + 150; PHh = 140
        w = mix(PHh, PW, pk); x = 960 - w / 2; y = 680 - PHh / 2
        glow(c, 960, 680, 620, TG, 0.16 * pk + 0.10 * env(t, 39.9, 40.05, 40.05, 41.2))
        sp = skia.Paint(AntiAlias=True, Color=col((0, 0, 0), 0.5 * pk)); sp.setMaskFilter(skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, 26))
        c.drawRRect(rrect(x, y + 16, w, PHh, PHh / 2), sp)
        rr = rrect(x, y, w, PHh, PHh / 2)
        fp = skia.Paint(AntiAlias=True)
        fp.setShader(skia.GradientShader.MakeLinear([skia.Point(x, y), skia.Point(x + w, y + PHh)], [col((20, 74, 112), 0.85 * pk), col((10, 30, 52), 0.9 * pk)]))
        c.drawRRect(rr, fp)
        st = skia.Paint(AntiAlias=True, Style=skia.Paint.kStroke_Style, StrokeWidth=2)
        st.setShader(skia.GradientShader.MakeLinear([skia.Point(x, y), skia.Point(x + w, y + PHh)], [col((150, 220, 255), 0.95 * pk), col(TG, 0.35 * pk), col((150, 220, 255), 0.7 * pk)], [0, .5, 1]))
        c.drawRRect(rr, st)
        c.save(); c.clipRRect(rr, True)
        tk = eo_expo(lin(t, 36.8, 37.6))
        draw_text(c, txt, 960, 680 + fs * 0.36, "InterDisplay-SemiBold", fs, WHITE, tk, "center", tracking=0.0 if tk > 0.995 else mix(0.14, 0.0, tk))
        for g0 in (37.6, 39.85):
            gk2 = lin(t, g0, g0 + 0.8)
            if 0 < gk2 < 1:
                gx = mix(x - 200, x + w + 200, eio_cubic(gk2))
                gp = skia.Paint(AntiAlias=True); gp.setBlendMode(skia.BlendMode.kPlus)
                gp.setShader(skia.GradientShader.MakeLinear([skia.Point(gx - 120, y), skia.Point(gx + 120, y + PHh)], [col(WHITE, 0), col(WHITE, 0.28), col(WHITE, 0)]))
                c.drawRect(skia.Rect(x, y, x + w, y + PHh), gp)
        c.restore()
    k = eo_cubic(lin(t, 37.3, 38.1))
    draw_text(c, "Open the link in Telegram to get started", 960, 808 + (1 - k) * 12, "InterDisplay-Regular", 28, (170, 180, 196), k, "center")
    k = eo_cubic(lin(t, 37.0, 38.0))
    draw_text(c, "Demo footage shown. Results are illustrative and not a guarantee of future returns. Crypto trading carries risk.",
              960, 1036, "Inter-Regular", 17, GRAY, 0.75 * k, "center")

def overlays(c, t):
    # brand bug + footage disclosure during product scenes
    a = env(t, 8.2, 8.9, 27.2, 27.6)
    if a > 0:
        draw_logo(c, LOGO, 84, 66, 38, a * 0.95)
        draw_text(c, "GrokBot", 114, 74, "InterDisplay-SemiBold", 23, WHITE, a * 0.92)
    a = env(t, 7.6, 8.4, 30.6, 31.1)
    if a > 0:
        draw_text(c, "DEMO FOOTAGE · ILLUSTRATIVE RESULTS", 64, 1040, "Inter-Medium", 14, GRAY, a * 0.8, tracking=0.2)

# ------------------------------------------------------------------ frame
def camx_at(t):
    return 1920 * eio_cubic(lin(t, 12.75, 13.65))

def wipe_x(t):
    return mix(-260, W + 260, eio_cubic(lin(t, 22.8, 23.4)))

_GRAIN = None
def grain(i):
    global _GRAIN
    if _GRAIN is None:
        r = np.random.default_rng(3); _GRAIN = [r.integers(-3, 4, size=(H, W, 1), dtype=np.int16) for _ in range(6)]
    return _GRAIN[i % 6]

def render_into(arr, t):
    surf = skia.Surface(arr)  # RGBA
    c = surf.getCanvas()
    cx = camx_at(t)
    draw_bg(c, t, cx)
    # global scene camera push (very subtle)
    push = 1 + 0.018 * math.sin(lin(t, 0, 8) * math.pi * 0.5) if t < 8 else 1.0
    c.save(); c.translate(960, 540); c.scale(push, push); c.translate(-960, -540)
    scene_open(c, t)
    c.restore()
    scene_dash(c, t, cx)
    # wipe between alerts (left: new) and positions
    wx = wipe_x(t)
    if 22.8 <= t <= 23.4:
        c.save(); c.clipRect(skia.Rect(wx, 0, W, H)); scene_agents_alerts(c, t, cx); c.restore()
        c.save(); c.clipRect(skia.Rect(0, 0, wx, H)); scene_positions(c, t); c.restore()
        bp = skia.Paint(AntiAlias=True); bp.setBlendMode(skia.BlendMode.kPlus)
        bp.setShader(skia.GradientShader.MakeLinear([skia.Point(wx - 160, 0), skia.Point(wx + 160, 0)], [col(ICE, 0), col(ICE, 0.16), col(ICE, 0)]))
        c.drawRect(skia.Rect(wx - 160, 0, wx + 160, H), bp)
        cp = skia.Paint(AntiAlias=True); cp.setBlendMode(skia.BlendMode.kPlus)
        cp.setShader(skia.GradientShader.MakeLinear([skia.Point(0, 0), skia.Point(0, H)], [col(WHITE, 0), col(WHITE, 0.75), col(WHITE, 0)]))
        c.drawRect(skia.Rect(wx - 1.2, 0, wx + 1.2, H), cp)
    else:
        scene_agents_alerts(c, t, cx)
        # positions with rack-focus exit
        o = eio_cubic(lin(t, 27.3, 27.85))
        if o > 0:
            lp = skia.Paint(); lp.setAlphaf(1 - o)
            if o > 0.01: lp.setImageFilter(skia.ImageFilters.Blur(16 * o, 16 * o))
            c.saveLayer(None, lp); c.translate(960, 540); sc = 1 - 0.06 * o; c.scale(sc, sc); c.translate(-960, -540)
            scene_positions(c, t); c.restore()
        else:
            scene_positions(c, t)
    scene_brand(c, t)
    scene_telegram(c, t)
    scene_cta(c, t)
    overlays(c, t)
    vignette(c)
    # fades
    fin = 1 - eo_cubic(lin(t, 0.0, 1.4)); fout = eio_cubic(lin(t, 41.35, 42.0))
    fa = max(fin, fout)
    if fa > 0: c.drawRect(skia.Rect(0, 0, W, H), skia.Paint(Color=col((0, 0, 0), fa)))
    surf.flushAndSubmit()
    del surf

# motion-blur windows (fast moves): sample several sub-frames
MB = [((1.85, 2.4), 6), ((4.1, 5.1), 4), ((6.5, 8.0), 8), ((8.0, 8.95), 6), ((12.7, 13.7), 10), ((17.6, 18.85), 8),
      ((22.75, 23.45), 6), ((27.3, 28.3), 4), ((30.45, 32.05), 14), ((35.3, 36.35), 6)]
def frame(t, idx=0):
    arr = np.zeros((H, W, 4), np.uint8)
    n = 1
    for (a, b), k in MB:
        if a <= t <= b: n = max(n, k)
    if n == 1:
        render_into(arr, t); acc = arr[..., :3].astype(np.int16)
    else:
        shutter = 0.5 / FPS
        accf = np.zeros((H, W, 3), np.float32)
        for k in range(n):
            arr[:] = 0
            ts = t - shutter / 2 + shutter * (k + 0.5) / n
            _DT[0] = ts - t
            render_into(arr, ts)
            accf += arr[..., :3]
        _DT[0] = 0.0
        acc = (accf / n + 0.5).astype(np.int16)
    acc += grain(idx)
    return np.clip(acc, 0, 255).astype(np.uint8)

if __name__ == "__main__":
    import sys, time
    out = sys.argv[1]; ts = [float(x) for x in sys.argv[2:]]
    for t in ts:
        t0 = time.time(); f = frame(t, int(t * FPS))
        Image.fromarray(f).save(os.path.join(out, "f_%05.2f.png" % t)); print(t, "%.2fs" % (time.time() - t0), flush=True)
