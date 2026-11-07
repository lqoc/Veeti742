"""3D camera + plane projection, SVG-path parser, ledger port and vector UI port of the GrokBot mobile app."""
import math, re
import numpy as np, skia
from ad import (clamp, lin, mix, col, draw_text, text_w, LOGO, SAMP, rrect)

# ------------------------------------------------------------------ 3D
def rot(yaw=0.0, pitch=0.0, roll=0.0):
    cy, sy, cp, sp, cr, sr = math.cos(yaw), math.sin(yaw), math.cos(pitch), math.sin(pitch), math.cos(roll), math.sin(roll)
    Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    Rx = np.array([[1, 0, 0], [0, cp, -sp], [0, sp, cp]])
    Rz = np.array([[cr, -sr, 0], [sr, cr, 0], [0, 0, 1]])
    return Ry @ Rx @ Rz

class Cam:
    """World: x right, y down, z forward (into screen). Camera looks along its +z."""
    def __init__(self, pos=(0, 0, -1400), yaw=0.0, pitch=0.0, roll=0.0, f=1400.0, cx=960.0, cy=540.0):
        self.pos = np.array(pos, float); self.R = rot(yaw, pitch, roll); self.f = f; self.cx = cx; self.cy = cy
    @staticmethod
    def look(pos, target, roll=0.0, f=1400.0):
        d = np.array(target, float) - np.array(pos, float)
        yaw = math.atan2(d[0], d[2]); pitch = -math.atan2(d[1], math.hypot(d[0], d[2]))
        return Cam(pos, yaw, pitch, roll, f)
    def to_cam(self, P): return (np.asarray(P, float) - self.pos) @ self.R
    def proj(self, P):
        d = self.to_cam(P); z = d[..., 2]
        zz = np.where(np.abs(z) < 1e-6, 1e-6, z)
        return np.stack([self.cx + self.f * d[..., 0] / zz, self.cy + self.f * d[..., 1] / zz], -1), z

def plane(cam, C, R, w, h, sw, sh, near=40.0):
    """Matrix mapping local (0..sw, 0..sh) onto a world rectangle (w x h) centred at C with rotation R.
    Returns (matrix, mean depth, facing) or None if behind camera."""
    loc = np.array([[-w / 2, -h / 2, 0], [w / 2, -h / 2, 0], [w / 2, h / 2, 0], [-w / 2, h / 2, 0]])
    world = np.asarray(C, float) + loc @ R.T
    scr, z = cam.proj(world)
    if (z < near).any(): return None
    m = skia.Matrix()
    ok = m.setPolyToPoly([skia.Point(0, 0), skia.Point(sw, 0), skia.Point(sw, sh), skia.Point(0, sh)],
                         [skia.Point(float(x), float(y)) for x, y in scr])
    if not ok: return None
    n = R @ np.array([0, 0, -1.0])
    facing = float(np.dot(n, cam.pos - np.asarray(C, float))) > 0
    bb = skia.Rect(float(scr[:, 0].min()), float(scr[:, 1].min()), float(scr[:, 0].max()), float(scr[:, 1].max()))
    return m, float(z.mean()), facing, bb

def on_plane(c, pl, fn, blur=0.0, alpha=1.0):
    if pl is None or alpha <= 0.003: return
    m = pl[0]
    if blur > 0.5 or alpha < 0.999:
        p = skia.Paint(); p.setAlphaf(clamp(alpha))
        if blur > 0.5: p.setImageFilter(skia.ImageFilters.Blur(blur, blur, skia.TileMode.kDecal))
        bb = pl[3]; pad = 3 * blur + 4
        c.saveLayer(skia.Rect(bb.left() - pad, bb.top() - pad, bb.right() + pad, bb.bottom() + pad), p)
    else:
        c.save()
    c.concat(m); fn(c)
    c.restore()

def poly_path(pts, close=True):
    p = skia.Path()
    for i, (x, y) in enumerate(pts):
        (p.moveTo if i == 0 else p.lineTo)(float(x), float(y))
    if close: p.close()
    return p

# ------------------------------------------------------------------ SVG path parser (M L H V Q A Z, abs/rel)
_TOK = re.compile(r"[MmLlHhVvQqAaZzCc]|-?(?:\d+\.?\d*|\.\d+)(?:e-?\d+)?")
def svg_path(d):
    toks = _TOK.findall(d); i = 0; p = skia.Path(); x = y = sx = sy = 0.0; cmd = None
    def num():
        nonlocal i; v = float(toks[i]); i += 1; return v
    while i < len(toks):
        if toks[i].isalpha():
            cmd = toks[i]; i += 1
            if cmd in "Zz": p.close(); x, y = sx, sy; continue
        rel = cmd.islower(); C = cmd.upper()
        if C == "M":
            nx, ny = num(), num()
            if rel: nx += x; ny += y
            p.moveTo(nx, ny); x, y = sx, sy = nx, ny; cmd = "l" if rel else "L"
        elif C == "L":
            nx, ny = num(), num()
            if rel: nx += x; ny += y
            p.lineTo(nx, ny); x, y = nx, ny
        elif C == "H":
            nx = num(); x = x + nx if rel else nx; p.lineTo(x, y)
        elif C == "V":
            ny = num(); y = y + ny if rel else ny; p.lineTo(x, y)
        elif C == "Q":
            a, b, nx, ny = num(), num(), num(), num()
            if rel: a += x; b += y; nx += x; ny += y
            p.quadTo(a, b, nx, ny); x, y = nx, ny
        elif C == "A":
            rx, ry, ang, la, sw, nx, ny = num(), num(), num(), num(), num(), num(), num()
            if rel: nx += x; ny += y
            p.arcTo(rx, ry, ang, skia.Path.ArcSize.kLarge_ArcSize if la else skia.Path.ArcSize.kSmall_ArcSize,
                    skia.PathDirection.kCW if sw else skia.PathDirection.kCCW, nx, ny)
            x, y = nx, ny
        else:
            raise ValueError(cmd)
    return p

# ------------------------------------------------------------------ ledger (exact port of buildLedger/stateAt)
COINS = ['Orbit Pup', 'Jelly Moon', 'Pixel Otter', 'Velvet Frog', 'Nova Bean', 'Tiny Comet', 'Peach Whale', 'Neon Fern', 'Cloud Moth',
         'Mango Meteor', 'Lunar Waffle', 'Cosmic Sprout', 'Amber Panda', 'Opal Gecko', 'Mint Moose', 'Solar Plum', 'Moon Kite',
         'Frost Finch', 'Neon Acorn', 'Plush Planet']
AG = [("Scanner", (255, 159, 10)), ("Analyst", (56, 139, 255)), ("Executor", (74, 222, 128)), ("Risk", (237, 120, 186))]
GREEN = (74, 222, 128); RED = (248, 113, 113)
def jround(x): return int(math.floor(x + 0.5))

def build_ledger(a=20.0, b=1450.0, start_minute=1320, minutes=300):
    start, end, count = jround(a * 100), jround(b * 100), 20
    trades, snaps, scans = [], [], []
    cash, realized, agents, scan = start, 0, [0, 0, 0, 0], 0
    snaps.append(dict(minute=0, cash=cash, equity=cash, realized=0, agents=agents[:]))
    for i in range(count):
        t = (i + 1) / count
        base = math.exp(math.log(start) * (1 - t) + math.log(end) * t)
        dip = .72 if i % 5 == 2 else .88 if i % 5 == 3 else 1
        nxt = end if i == count - 1 else max(1, jround(base * (1 + (dip - 1) * math.sin(math.pi * t))))
        begin = i / count * minutes; buy = begin + minutes / count * .08
        sell = (i + 1) / count * minutes - (0 if i == count - 1 else minutes / count * .05)
        cost = min(cash, max(1, jround(cash * .55), cash - nxt)); profit = nxt - cash; remaining = cash - cost; proceeds = nxt - remaining
        reviewed = 83 + (i * 37) % 79; scan += reviewed
        trades.append(dict(id=i, token=COINS[i], agent=i % 4, buy=buy, sell=sell, cost=cost, profit=profit, score=62 + (i * 13) % 35, reviewed=reviewed))
        for k in range(1, reviewed + 1): scans.append((begin + (buy - begin) * k / reviewed, scan - reviewed + k))
        cash = remaining
        snaps.append(dict(minute=buy, cash=cash, equity=cash + cost, realized=realized, agents=agents[:]))
        for fr in (.35, .72):
            snaps.append(dict(minute=buy + (sell - buy) * fr, cash=cash, equity=cash + cost + jround(profit * fr), realized=realized, agents=agents[:]))
        cash += proceeds; realized += profit; agents[i % 4] += profit
        snaps.append(dict(minute=sell, cash=cash, equity=cash, realized=realized, agents=agents[:]))
    return dict(start=start, end=end, start_minute=start_minute, minutes=minutes, trades=trades, snaps=snaps, scans=scans)

LED = build_ledger()
PLAYBACK_MS = 20000
def fmt_time(m):
    m = int(math.floor(m)) % 1440; return "%02d:%02d" % (m // 60, m % 60)
def clock_at(L, p): return fmt_time(L["start_minute"] + math.floor(clamp(p) * L["minutes"]))
def money(c): return "$" + "{:,.2f}".format(abs(c) / 100)
def signed(c): return ("−" if c < 0 else "+" if c > 0 else "") + money(abs(c))
def ret_label(v, s): return "{:,.2f}%".format((v / s - 1) * 100)
def mult_label(v, s): return "{:.2f}×".format(v / s)

def state_at(L, p):
    p = clamp(p); minute = p * L["minutes"]; S = L["snaps"]; i = 0
    while i + 1 < len(S) and S[i + 1]["minute"] <= minute: i += 1
    a, b = S[i], S[min(i + 1, len(S) - 1)]
    mx = 0 if a["minute"] == b["minute"] else (minute - a["minute"]) / (b["minute"] - a["minute"])
    eq = jround(a["equity"] + (b["equity"] - a["equity"]) * mx)
    closed = [t for t in L["trades"] if t["sell"] <= minute]; opened = [t for t in L["trades"] if t["buy"] <= minute]
    sc = 0
    for m_, tot in L["scans"]:
        if m_ > minute: break
        sc = tot
    nt = [t for t in closed if t["id"] % 4 == 3]
    notice = nt[-1] if nt and (minute - nt[-1]["sell"]) / L["minutes"] * PLAYBACK_MS < 2000 and p < 1 else None
    return dict(p=p, minute=minute, equity=eq, profit=eq - L["start"], agents=a["agents"], closed=closed, opened=opened, scans=sc,
                score=opened[-1]["score"] if opened else 0, time=clock_at(L, p), notice=notice)

def equity_at_minute(L, minute):
    S = L["snaps"]; i = 0
    while i + 1 < len(S) and S[i + 1]["minute"] <= minute: i += 1
    a, b = S[i], S[min(i + 1, len(S) - 1)]
    mx = 0 if a["minute"] == b["minute"] else (minute - a["minute"]) / (b["minute"] - a["minute"])
    return a["equity"] + (b["equity"] - a["equity"]) * mx

def chart_bounds(L):
    v = [s["equity"] for s in L["snaps"]]; lo, hi = min(v), max(v); r = max(1, hi - lo)
    return max(0, lo - r * .08), hi + r * .13

# ------------------------------------------------------------------ vector UI (port of drawVideo, 390 x 694 logical)
def hexc(h, a=1.0):
    h = h.lstrip("#"); return col((int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)), a)
WF = {400: "Inter-Regular", 500: "Inter-Medium", 550: "Inter-Medium", 600: "Inter-SemiBold", 650: "Inter-SemiBold"}
def txt(c, s, x, y, size, color="#ffffff", weight=400, align="left", a=1.0):
    rgb = color if isinstance(color, tuple) else tuple(int(color.lstrip("#")[k:k + 2], 16) for k in (0, 2, 4))
    draw_text(c, str(s), x, y, WF[weight], size, rgb, a, align)
def box(c, x, y, w, h, r, color, a=1.0):
    p = skia.Paint(AntiAlias=True, Color=hexc(color, a) if isinstance(color, str) else col(color, a)); c.drawRRect(rrect(x, y, w, h, r), p)
def stroke_paint(color, w, a=1.0, dash=None):
    p = skia.Paint(AntiAlias=True, Style=skia.Paint.kStroke_Style, StrokeWidth=w,
                   Color=hexc(color, a) if isinstance(color, str) else col(color, a))
    p.setStrokeCap(skia.Paint.kRound_Cap); p.setStrokeJoin(skia.Paint.kRound_Join)
    if dash: p.setPathEffect(skia.DashPathEffect.Make(dash[0], dash[1]))
    return p
def logo_img(c, x, y, s, r):
    c.save(); c.clipRRect(rrect(x, y, s, s, r), True)
    c.drawImageRect(LOGO, skia.Rect(0, 0, LOGO.width(), LOGO.height()), skia.Rect.MakeXYWH(x, y, s, s), SAMP, skia.Paint(AntiAlias=True))
    c.restore()

NAV = [("Home", ["m3 10 9-8 9 8v11h-6v-7H9v7H3Z"], None),
       ("Agents", ["M12 2v4M1 11v4M23 11v4M8 11v3M16 11v3M9 17h6"], (4, 6, 16, 13, 4)),
       ("Activity", ["M2 13h5l3-9 4 16 3-9h5"], None),
       ("Account", ["M4 22v-3a8 8 0 0 1 16 0v3"], "circle")]
NAV_P = [[svg_path(d) for d in ds] for _, ds, _ in NAV]

def agent_icon(c, i, cx, cy, s=1.0, a=1.0):
    rgb = AG[i][1]
    if i == 0:  # crosshair
        p = stroke_paint(rgb, 1.3 * s, a); c.drawCircle(cx, cy, 4.2 * s, p)
        for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)): c.drawLine(cx + dx * 2.2 * s, cy + dy * 2.2 * s, cx + dx * 7 * s, cy + dy * 7 * s, p)
    elif i == 1:  # target dot
        c.drawCircle(cx, cy, 6.2 * s, stroke_paint(rgb, 1.2 * s, a)); c.drawCircle(cx, cy, 4.2 * s, skia.Paint(AntiAlias=True, Color=col(rgb, a)))
    elif i == 2:  # arrow
        p = stroke_paint(rgb, 1.5 * s, a); c.drawLine(cx - 5 * s, cy + 5 * s, cx + 5 * s, cy - 5 * s, p)
        c.drawLine(cx + 5 * s, cy - 5 * s, cx - 0.5 * s, cy - 5 * s, p); c.drawLine(cx + 5 * s, cy - 5 * s, cx + 5 * s, cy + 0.5 * s, p)
    else:  # diamond
        c.drawPath(poly_path([(cx, cy - 6.5 * s), (cx + 6.5 * s, cy), (cx, cy + 6.5 * s), (cx - 6.5 * s, cy)]), stroke_paint(rgb, 1.2 * s, a))

AG_LABELS = ['Tokens scanned', 'Signal score', 'Positions closed', 'Checks completed']
def agent_metrics(s):
    return ["{:,}".format(s["scans"]), str(s["score"]), str(len(s["closed"])), "{:,}".format(sum(t["reviewed"] for t in s["opened"]))]

def draw_agent_card(c, i, s, x=0.0, y=0.0, spin=0.0, hi=0.0):
    """One agent card, 145 x 94 logical, top-left at (x,y)."""
    rgb = AG[i][1]
    box(c, x, y, 145, 94, 13, "#1c1c1e")
    c.drawRRect(rrect(x + .5, y + .5, 144, 93, 12.5), stroke_paint((60, 60, 66), 0.8, 0.6 + 0.4 * hi))
    if hi > 0:
        c.drawRRect(rrect(x + .5, y + .5, 144, 93, 12.5), stroke_paint(rgb, 1.0, 0.85 * hi))
    txt(c, AG[i][0], x + 11, y + 19, 10, "#ededf1", 600)
    agent_icon(c, i, x + 128, y + 15)
    txt(c, AG_LABELS[i], x + 11, y + 37, 8, "#85858e")
    txt(c, agent_metrics(s)[i], x + 11, y + 60, 21, "#ffffff", 600)
    rp = stroke_paint(rgb, 1, 1, ([2, 3], spin)); c.drawCircle(x + 119, y + 51, 11, rp)
    txt(c, s["score"] if i == 1 else len(s["closed"]), x + 119, y + 54, 7, rgb, 550, "center")
    c.drawCircle(x + 13, y + 79, 2, skia.Paint(AntiAlias=True, Color=col(GREEN)))
    ac = s["agents"][i]
    txt(c, signed(ac), x + 20, y + 82, 8, RED if ac < 0 else GREEN, 550)
    txt(c, "Done" if s["p"] >= 1 else "Working", x + 133, y + 82, 6, "#777781", 400, "right")

def draw_notice(c, s_text, x=12.0, y=38.0):
    box(c, x, y, 366, 49, 17, "#202022")
    logo_img(c, x + 12, y + 10, 28, 8)
    txt(c, "Grok Bot", x + 50, y + 18, 11, "#ffffff", 650)
    txt(c, "now", x + 350, y + 17, 9, "#8b8b94", 400, "right")
    txt(c, s_text, x + 50, y + 34, 9, "#dddde3")

def notice_text(t): return ("Stop sell" if t["profit"] < 0 else "Profit taken") + " · " + t["token"] + " " + signed(t["profit"])

SECTIONS = [("status", 0, 34), ("notice", 34, 90), ("header", 90, 126), ("balance", 126, 220), ("chart", 220, 346),
            ("tf", 346, 396), ("agents", 396, 528), ("activity", 528, 642), ("nav", 642, 694)]

def draw_section(c, name, s, L=LED, card_scroll=None):
    if name == "status":
        txt(c, s["time"].lstrip("0") if s["time"][0] == "0" else s["time"], 26, 23, 13, "#ffffff", 600)
        box(c, 143, 7, 104, 25, 15, "#111111")
        wp = skia.Paint(AntiAlias=True, Color=skia.ColorWHITE)
        for i in range(4): c.drawRect(skia.Rect.MakeXYWH(305 + i * 4, 20 - i * 2, 2.7, 4 + i * 2), wp)
        for r in (7, 4):
            pa = skia.Path(); pa.addArc(skia.Rect(333 - r, 22 - r, 333 + r, 22 + r), 216, 108); c.drawPath(pa, stroke_paint("#ffffff", 1.5))
        box(c, 346, 12, 22, 11, 3, "#7a7a80"); box(c, 347.2, 13.2, 17.8, 8.6, 2, "#ffffff"); box(c, 369, 15, 2, 5, 1, "#888888")
    elif name == "notice":
        if s.get("notice"): draw_notice(c, notice_text(s["notice"]))
    elif name == "header":
        logo_img(c, 18, 95, 28, 8); txt(c, "Grok Bot", 55, 114, 18, "#ffffff", 650)
        p = stroke_paint("#dddddd", 1.3); c.drawCircle(337, 106, 5, p); c.drawLine(341, 110, 346, 115, p)
        c.drawPath(svg_path("M359 113L361 109L361 104Q365 97 369 104L369 109L371 113Z"), p)
    elif name == "balance":
        txt(c, "Total balance", 18, 143, 11, "#8e8e93"); txt(c, "USD", 371, 142, 9, "#72727a", 400, "right")
        txt(c, money(s["equity"]), 16, 188, 48, "#ffffff", 600)
        pc = RED if s["profit"] < 0 else GREEN
        txt(c, signed(s["profit"]) + " (" + ret_label(s["equity"], L["start"]) + ")", 18, 210, 11, pc, 600)
        box(c, 286, 195, 85, 22, 6, "#102b1b"); txt(c, mult_label(s["equity"], L["start"]), 329, 210, 10, GREEN, 600, "center")
    elif name == "chart":
        lo, hi = chart_bounds(L); left, right, top, bottom = 18, 371, 237, 334
        X = lambda m: left + m / L["minutes"] * (right - left)
        Y = lambda e: bottom - (e - lo) / (hi - lo) * (bottom - top)
        pts = [(sn["minute"], sn["equity"]) for sn in L["snaps"] if sn["minute"] <= s["minute"]]
        if s["p"] < 1: pts.append((s["minute"], s["equity"]))
        c.save(); c.clipRect(skia.Rect.MakeXYWH(left - 1, 222, right - left + 3, 122))
        if len(pts) > 1:
            ar = poly_path([(X(m), Y(e)) for m, e in pts] + [(X(s["minute"]), 342), (left, 342)])
            gp = skia.Paint(AntiAlias=True)
            gp.setShader(skia.GradientShader.MakeLinear([skia.Point(0, top), skia.Point(0, 342)], [col(GREEN, 0.24), col(GREEN, 0)]))
            c.drawPath(ar, gp)
            c.drawPath(poly_path([(X(m), Y(e)) for m, e in pts], False), stroke_paint(GREEN, 2))
        c.drawCircle(X(s["minute"]), Y(s["equity"]), 2.4, skia.Paint(AntiAlias=True, Color=col(GREEN)))
        for t in s["closed"][-3:]:
            sn = next(v for v in L["snaps"] if v["minute"] == t["sell"])
            xx, yy = X(t["sell"]), Y(sn["equity"]); tx, ty = min(307, max(18, xx - 28)), max(224, yy - 25)
            cc = RED if t["profit"] < 0 else GREEN
            c.drawLine(xx, yy, xx, ty + 16, stroke_paint(cc, .7, 1, ([2, 3], 0)))
            c.drawCircle(xx, yy, 2.4, skia.Paint(AntiAlias=True, Color=col(cc)))
            box(c, tx, ty, 64, 17, 4, "#07120b"); c.drawRRect(rrect(tx, ty, 64, 17, 4), stroke_paint(cc, .7))
            txt(c, signed(t["profit"]), tx + 32, ty + 11, 7.5, cc, 550, "center")
        c.restore()
    elif name == "tf":
        box(c, 18, 350, 46, 20, 6, "#102b1b")
        c.drawCircle(28.5, 360.5, 2.2, skia.Paint(AntiAlias=True, Color=col(GREEN))); txt(c, "LIVE", 45, 364, 8, GREEN, 600, "center")
        for i, v in enumerate(['1H', '4H', '1D', '1W', 'ALL']): txt(c, v, 96 + i * 65, 364, 9, "#777780", 550, "center")
        txt(c, s["time"], 18, 389, 9, "#b2b2bc", 550); txt(c, clock_at(L, 0) + " → " + clock_at(L, 1), 371, 389, 8, "#6f6f78", 400, "right")
    elif name == "agents":
        txt(c, "Agents", 18, 416, 16, "#ffffff", 600); c.drawCircle(79, 411, 2, skia.Paint(AntiAlias=True, Color=col(GREEN)))
        txt(c, "{:,} scanned".format(s["scans"]), 371, 415, 8, "#7c7c86", 400, "right")
        scroll = (1 - math.cos(s["p"] * 2 * math.pi)) / 2 * 267 if card_scroll is None else card_scroll
        c.save(); c.clipRect(skia.Rect.MakeXYWH(18, 428, 353, 97))
        for i in range(4): draw_agent_card(c, i, s, 18 + i * 155 - scroll, 429)
        c.restore()
    elif name == "activity":
        txt(c, "Activity", 18, 550, 16, "#ffffff", 600); txt(c, "%d trades" % len(s["closed"]), 371, 549, 8, "#7c7c86", 400, "right")
        rec = s["closed"][-2:][::-1]
        if not rec: txt(c, "Scanning new pairs…", 18, 584, 10, "#686873")
        for i, t in enumerate(rec):
            yy = 568 + i * 39; ag = AG[t["agent"]][1]; loss = t["profit"] < 0
            box(c, 18, yy, 27, 27, 14, "#252128"); txt(c, t["token"][0], 31.5, yy + 18, 12, ag, 650, "center")
            txt(c, ("Stop sell " if loss else "Sold ") + t["token"], 55, yy + 11, 10, "#ededf1", 550)
            txt(c, fmt_time(L["start_minute"] + t["sell"]) + " · " + ("Risk protection" if loss else "Profit secured"), 55, yy + 25, 8, "#76767f")
            txt(c, signed(t["profit"]), 371, yy + 13, 10, RED if loss else GREEN, 600, "right"); txt(c, "USDC", 371, yy + 27, 7, "#73737d", 400, "right")
    elif name == "nav":
        c.drawLine(0, 646, 390, 646, stroke_paint("#1d1d21", .6))
        for i, (lab, _, extra) in enumerate(NAV):
            xx = 48 + i * 98; colr = "#ffffff" if i == 0 else "#666670"
            c.save(); c.translate(xx - 9, 652); c.scale(0.75, 0.75)
            p = stroke_paint(colr, 1.5 / 0.75 * 0.75 * 1.33)
            for pa in NAV_P[i]:
                if i == 0:
                    c.drawPath(pa, skia.Paint(AntiAlias=True, Color=hexc(colr)))
                c.drawPath(pa, p)
            if extra == "circle": c.drawCircle(12, 8, 4, p)
            elif extra: c.drawRRect(rrect(4, 6, 16, 13, 4), p)
            c.restore()
            txt(c, lab, xx, 679, 7, colr, 400, "center")
        c.drawRect(skia.Rect(0, 683, 390, 694), skia.Paint(Color=skia.ColorBLACK))
        txt(c, "ILLUSTRATIVE DATA", 195, 691, 7.5, "#a1a1ab", 650, "center")

def draw_ui(c, s, L=LED, skip=()):
    c.drawRect(skia.Rect(0, 0, 390, 694), skia.Paint(Color=skia.ColorBLACK))
    for name, _, _ in SECTIONS:
        if name not in skip: draw_section(c, name, s, L)
