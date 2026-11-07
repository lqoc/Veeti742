"""Original score + sound design for the GrokBot promo (all synthesised, no samples). 48 kHz stereo WAV."""
import numpy as np, sys
from scipy import signal
from scipy.io import wavfile

SR = 48000; DUR = 52.0; N = int(SR * DUR)
BPM = 120; BEAT = 60 / BPM; BAR = 4 * BEAT
rng = np.random.default_rng(5)
L = np.zeros(N); R = np.zeros(N)            # dry bus
RVL = np.zeros(N); RVR = np.zeros(N)        # reverb send
DUCK = np.ones(N)                           # sidechain gain for music bus

def midi(n): return 440.0 * 2 ** ((n - 69) / 12)
def idx(t): return int(round(t * SR))

def add(sig, t0, pan=0.0, gain=1.0, rev=0.0, bus=None):
    i0 = idx(t0)
    if i0 >= N: return
    if i0 < 0: sig = sig[..., -i0:]; i0 = 0
    sig = sig[..., :N - i0]
    if sig.ndim == 1:
        gl = np.cos((pan + 1) * np.pi / 4) * gain; gr = np.sin((pan + 1) * np.pi / 4) * gain
        l, r = sig * gl * 1.414, sig * gr * 1.414
    else:
        l, r = sig[0] * gain, sig[1] * gain
    tl, tr = (L, R) if bus is None else bus
    tl[i0:i0 + len(l)] += l; tr[i0:i0 + len(r)] += r
    if rev > 0:
        RVL[i0:i0 + len(l)] += l * rev; RVR[i0:i0 + len(r)] += r * rev

def tt(d): return np.arange(int(d * SR)) / SR

def saw(f, d, phase=0.0):
    """PolyBLEP band-limited saw; f may be array."""
    n = int(d * SR)
    f = np.broadcast_to(np.asarray(f, float), (n,)) if np.ndim(f) == 0 else f[:n]
    dt = f / SR
    ph = (phase + np.cumsum(dt)) % 1.0
    y = 2 * ph - 1
    m = ph < dt; x = ph[m] / dt[m]; y[m] -= x + x - x * x - 1
    m = ph > 1 - dt; x = (ph[m] - 1) / dt[m]; y[m] -= x * x + x + x + 1
    return y

def sine(f, d, phase=0.0):
    n = int(d * SR)
    f = np.broadcast_to(np.asarray(f, float), (n,)) if np.ndim(f) == 0 else f[:n]
    return np.sin(2 * np.pi * np.cumsum(f) / SR + phase)

def noise(d): return rng.standard_normal(int(d * SR))

def adsr(d, a=0.01, dcy=0.1, s=0.7, r=0.2):
    n = int(d * SR); e = np.ones(n) * s
    na, nd, nr = int(a * SR), int(dcy * SR), int(r * SR)
    na = max(na, 1)
    e[:na] = np.linspace(0, 1, na)
    if nd: e[na:na + nd] = np.linspace(1, s, len(e[na:na + nd]))
    if nr: e[-nr:] *= np.linspace(1, 0, nr)
    return e

def expdec(d, tau): return np.exp(-tt(d) / tau)

def lp(x, fc, order=2):
    sos = signal.butter(order, min(fc, SR * 0.45), "low", fs=SR, output="sos"); return signal.sosfilt(sos, x)
def hp(x, fc, order=2):
    sos = signal.butter(order, fc, "high", fs=SR, output="sos"); return signal.sosfilt(sos, x)
def bp(x, f1, f2, order=2):
    sos = signal.butter(order, [f1, min(f2, SR * 0.45)], "band", fs=SR, output="sos"); return signal.sosfilt(sos, x)

def sweep_lp(x, fc_curve, block=256, q=0.9):
    """Time-varying resonant low-pass (biquad per block, state carried)."""
    y = np.zeros_like(x); zi = np.zeros(2)
    for i in range(0, len(x), block):
        fc = float(np.clip(fc_curve[min(i, len(fc_curve) - 1)], 30, SR * 0.45))
        w0 = 2 * np.pi * fc / SR; al = np.sin(w0) / (2 * q); cw = np.cos(w0)
        b = np.array([(1 - cw) / 2, 1 - cw, (1 - cw) / 2]); a = np.array([1 + al, -2 * cw, 1 - al])
        b /= a[0]; a /= a[0]
        y[i:i + block], zi = signal.lfilter(b, a, x[i:i + block], zi=zi)
    return y

def sweep_bp(x, fc_curve, block=256, q=2.0):
    y = np.zeros_like(x); zi = np.zeros(2)
    for i in range(0, len(x), block):
        fc = float(np.clip(fc_curve[min(i, len(fc_curve) - 1)], 40, SR * 0.45))
        w0 = 2 * np.pi * fc / SR; al = np.sin(w0) / (2 * q); cw = np.cos(w0)
        b = np.array([al, 0, -al]); a = np.array([1 + al, -2 * cw, 1 - al]); b /= a[0]; a /= a[0]
        y[i:i + block], zi = signal.lfilter(b, a, x[i:i + block], zi=zi)
    return y

def curve(points, n):
    """piecewise-linear automation (time,value) list -> array of n samples (log-friendly values ok)."""
    ts = np.array([p[0] for p in points]); vs = np.array([p[1] for p in points])
    return np.interp(np.arange(n) / SR, ts, vs)

# ------------------------------------------------------------------ SHOWREEL ARRANGEMENT (appended to helper header)
CHORDS = [[57, 60, 64, 69], [53, 57, 60, 65], [55, 60, 64, 67], [55, 59, 62, 67]]
ROOTS = [33, 29, 36, 31]
def chord_at(t): return int(t // BAR) % 4
MUSIC_L = np.zeros(N); MUSIC_R = np.zeros(N); MB = (MUSIC_L, MUSIC_R)
DRUMS_L = np.zeros(N); DRUMS_R = np.zeros(N); DB = (DRUMS_L, DRUMS_R)

def section(t):
    if t < 4: return "intro"
    if t < 8: return "logo"
    if t < 35.25: return "groove" if not (30.2 <= t < 35.25) else "alerts"
    if t < 40: return "break"
    if t < 50: return "final"
    return "end"
def kick_on(t): return (8.0 <= t < 35.25) or (40.0 <= t < 50.0) or (30.2 <= t < 35.25)

# ---- pad
pad_cut = curve([(0, 220), (4, 500), (7.9, 1800), (8, 2400), (30, 2600), (35.25, 900), (39.9, 3800), (40, 3000), (50, 3400), (52, 900)], N)
pad = np.zeros((2, N))
for b in range(26):
    t0 = b * BAR
    if t0 >= 50: break
    ch = CHORDS[b % 4]; last = t0 + BAR >= 50
    d = BAR + 0.9 if not last else 4.0
    for k, n in enumerate(ch):
        for j, det in enumerate((-0.12, 0.0, 0.13)):
            v = saw(midi(n) * 2 ** (det / 12), d, rng.uniform()) * adsr(d, 0.4, 0.4, 0.8, 0.9 if not last else 3.0)
            pan = (-0.65, 0.0, 0.65)[j]
            i0 = idx(t0); seg = v[:N - i0]
            pad[0, i0:i0 + len(seg)] += seg * np.cos((pan + 1) * np.pi / 4); pad[1, i0:i0 + len(seg)] += seg * np.sin((pan + 1) * np.pi / 4)
pad[:, idx(50.0):] *= np.linspace(1, 0, N - idx(50.0)) ** 3
for ch_ in range(2): pad[ch_] = sweep_lp(pad[ch_], pad_cut, q=0.8)
pad_vol = curve([(0, 0), (1.5, 0.04), (4, 0.06), (8, 0.05), (35.25, 0.05), (36, 0.08), (39.9, 0.09), (40, 0.055), (50, 0.055), (52, 0.02)], N)
add(pad * pad_vol, 0, bus=MB)
RVL += pad[0] * pad_vol * 0.35; RVR += pad[1] * pad_vol * 0.35

# ---- intro drone + air + glitch ticks
dr = (sine(midi(33), 4.6) * 0.5 + sine(midi(40), 4.6) * 0.2 + lp(noise(4.6), 300) * 0.1) * curve([(0, 0), (0.6, 0.14), (3.6, 0.16), (4.3, 0)], int(4.6 * SR))
add(dr, 0)
air = hp(noise(8.2), 3500) * curve([(0, 0), (1.0, 0.012), (7.6, 0.014), (8.1, 0)], int(8.2 * SR))
add(np.vstack([air, np.roll(air, 700)]), 0)
r2 = np.random.default_rng(3)
for i in range(32):
    tt_ = i * 0.125
    if r2.random() < 0.45 and tt_ > 0.4:
        d = 0.02 + 0.03 * r2.random()
        add(hp(noise(d), 4000) * np.exp(-tt(d) / 0.006) * (sine(r2.uniform(800, 3000), d) * 0.5 + 0.5), tt_, pan=r2.uniform(-.8, .8), gain=0.035)

# ---- arp (16ths) with ping-pong delay
arp = np.zeros((2, N)); pattern = [0, 1, 2, 3, 2, 1, 0, 2, 3, 2, 1, 3, 0, 1, 2, 3]; s16 = BEAT / 4
t = 4.0
while t < 50.0 - 1e-6:
    k = int(round(t / s16)); ch = CHORDS[chord_at(t)]
    n = ch[pattern[k % 16]] + 12 + (12 if (k % 32) >= 28 else 0)
    d = 0.2; tone = saw(midi(n), d) * 0.6 + sine(midi(n) * 2, d) * 0.25
    open_ = 0.5 + 0.5 * min(1, max(0, (t - 4) / 4))
    if 24.05 <= t < 30.2: open_ *= 1 + 0.6 * (t - 24.05) / 6
    v = sweep_lp(tone, (900 + 4200 * expdec(d, 0.03)) * open_, block=64, q=1.2) * expdec(d, 0.05)
    vel = 0.75 + 0.25 * (k % 4 == 0); i0 = idx(t); seg = v[:N - i0] * vel; pan = 0.3 * np.sin(k * 0.7)
    arp[0, i0:i0 + len(seg)] += seg * (0.7 - pan * 0.4); arp[1, i0:i0 + len(seg)] += seg * (0.7 + pan * 0.4)
    t += s16
dl = int(0.375 * SR); d1 = np.zeros_like(arp)
d1[1, dl:] += arp[0, :-dl] * 0.38; d1[0, 2 * dl:] += arp[1, :-2 * dl] * 0.25
arp = arp + lp(d1, 3500)
arp_vol = curve([(4, 0), (6, 0.045), (7.9, 0.08), (8, 0.075), (30.2, 0.075), (35.25, 0.10), (39.9, 0.11), (40, 0.085), (50, 0.085)], N)
add(arp * arp_vol, 0, bus=MB); RVL += arp[0] * arp_vol * 0.25; RVR += arp[1] * arp_vol * 0.25

# ---- bass: offbeat 8ths + sub
t = 4.0
while t < 50.0 - 1e-6:
    sec = section(t)
    if sec in ("logo", "groove", "alerts", "final"):
        kk = int(round(t / (BEAT / 2)))
        root = ROOTS[chord_at(t)] + (12 if kk % 2 else 0)
        d = BEAT / 2 * 0.9
        v = saw(midi(root), d) * 0.7 + saw(midi(root) * 1.005, d) * 0.4 + sine(midi(root), d) * 0.6
        cut = 380 if sec == "logo" else 650 if sec != "final" else 900
        v = lp(v, cut) * adsr(d, 0.004, 0.08, 0.7, 0.03)
        add(np.tanh(v * 1.4), t, gain=0.17 if sec != "logo" else 0.10, bus=MB)
    t += BEAT / 2

# ---- drums
def kick():
    d = 0.5; f = 44 + 120 * np.exp(-tt(d) / 0.03)
    v = sine(f, d) * np.exp(-tt(d) / 0.2); click = hp(noise(0.012), 2000) * np.linspace(1, 0, int(0.012 * SR)) * 0.3
    v[:len(click)] += click; return np.tanh(v * 1.8)
def clap():
    d = 0.3; v = np.zeros(int(d * SR))
    for o in (0, 0.009, 0.018):
        i = int(o * SR); n_ = bp(noise(d - o), 1100, 5200) * np.exp(-tt(d - o) / (0.012 if o < 0.018 else 0.1))
        v[i:i + len(n_)] += n_[:len(v) - i]
    return v
def hat(d=0.06, tau=0.018): return hp(noise(d), 7000, 4) * np.exp(-tt(d) / tau)
K, C_, HH, OH = kick(), clap(), hat(), hat(0.3, 0.09)
t = 8.0
while t < 50.0 - 1e-6:
    if kick_on(t):
        b = int(round(t / BEAT)); alerts = 30.2 <= t < 35.25
        add(K, t, gain=0.60 if not alerts else 0.45, bus=DB)
        i0 = idx(t); n_ = idx(0.42)
        dk = 1 - 0.7 * np.exp(-np.arange(n_) / (0.11 * SR)) * (1 - np.exp(-np.arange(n_) / (0.004 * SR)))
        DUCK[i0:i0 + n_] = np.minimum(DUCK[i0:i0 + n_], dk[:N - i0])
        if b % 2 == 1 and not alerts: add(C_, t, gain=0.20, bus=DB, rev=0.25)
        add(HH, t + BEAT / 2, gain=0.085, pan=0.25, bus=DB)
        if (14.1 <= t < 30.2) or (40 <= t < 50):
            add(HH, t + BEAT / 4, gain=0.035, pan=-0.3, bus=DB); add(HH, t + 3 * BEAT / 4, gain=0.035, pan=-0.3, bus=DB)
        if b % 8 == 7 and not alerts: add(OH, t + BEAT / 2, gain=0.05, bus=DB, rev=0.2)
    elif 35.25 <= t < 40:
        add(HH, t + BEAT / 2, gain=0.05, pan=0.25, bus=DB)
    t += BEAT
for i in range(16): add(C_, 39.0 + i / 16, gain=0.03 + 0.11 * (i / 15) ** 2, bus=DB, rev=0.2)    # build into 40
for i in range(8): add(C_, 23.55 + i / 16, gain=0.03 + 0.08 * (i / 7) ** 2, bus=DB, rev=0.2)     # whip fill
for tc in (8.0, 14.1, 24.05, 40.0):
    add(hp(noise(1.6), 5000, 2) * np.exp(-tt(1.6) / 0.45), tc, gain=0.06, rev=0.4, bus=DB)

# ------------------------------------------------------------------ SFX
def whoosh(t0, d, f0=300, f1=4000, gain=0.18, pan0=-0.6, pan1=0.6, q=1.6):
    n = int(d * SR); x = noise(d); fc = np.exp(np.linspace(np.log(f0), np.log(f1), n))
    y = sweep_bp(x, fc, q=q) * np.sin(np.linspace(0, np.pi, n)) ** 1.6; pan = np.linspace(pan0, pan1, n)
    add(np.vstack([y * np.cos((pan + 1) * np.pi / 4), y * np.sin((pan + 1) * np.pi / 4)]) * 1.414, t0, gain=gain, rev=0.25)
def impact(t0, gain=0.5, size=1.0):
    d = 2.2 * size; f = 32 + 55 * np.exp(-tt(d) / 0.12)
    boom = sine(f, d) * np.exp(-tt(d) / (0.55 * size)); hit = lp(noise(d), 1800) * np.exp(-tt(d) / 0.06) * 0.6
    add(np.tanh((boom + hit) * 1.3), t0, gain=gain, rev=0.35 * size)
def braam(t0, gain=0.25, d=2.2):
    v = np.zeros(int(d * SR))
    for n in (33, 40, 45):
        for det in (-0.15, 0, 0.15): v += saw(midi(n) * 2 ** (det / 12), d)
    v = sweep_lp(v, curve([(0, 2400), (0.25, 900), (d, 300)], int(d * SR)), q=1.4) * np.exp(-tt(d) / 0.9)
    add(np.tanh(v * 0.5), t0, gain=gain, rev=0.4)
def riser(t0, t1, gain=0.12, f0=200, f1=2400):
    d = t1 - t0; n = int(d * SR); x = noise(d); fc = np.exp(np.linspace(np.log(f0 * 2), np.log(f1 * 3), n))
    y = sweep_bp(x, fc, q=3.0) * np.linspace(0, 1, n) ** 2.2
    y = y + lp(saw(np.exp(np.linspace(np.log(f0), np.log(f1), n)), d) * np.linspace(0, 1, n) ** 3 * 0.15, 5000)
    add(np.vstack([y, np.roll(y, 300)]), t0, gain=gain, rev=0.3)
def reverse_swell(t1, d=1.2, gain=0.08):
    x = hp(noise(d), 2500, 2) * np.linspace(0, 1, int(d * SR)) ** 3
    add(np.vstack([x, np.roll(x, 500)]), t1 - d, gain=gain, rev=0.3)
def blip(t0, f=1320, gain=0.06, pan=0.3, two=False):
    d = 0.22; v = (sine(f, d) + 0.3 * sine(f * 2.01, d)) * np.exp(-tt(d) / 0.04); add(v, t0, pan=pan, gain=gain, rev=0.25)
    if two: add((sine(f * 1.5, d) + 0.3 * sine(f * 3.0, d)) * np.exp(-tt(d) / 0.05), t0 + 0.085, pan=pan, gain=gain * 0.9, rev=0.3)
def glint(t0, gain=0.05):
    d = 1.2; v = np.zeros(int(d * SR))
    for i, f in enumerate((2637, 3520, 4186, 5274)):
        o = int(i * 0.035 * SR); vv = sine(f * (1 + 0.002 * i), d - i * 0.035) * np.exp(-tt(d - i * 0.035) / 0.25); v[o:o + len(vv)] += vv * (0.6 ** i)
    add(np.vstack([v, np.roll(v, 600)]), t0, gain=gain, rev=0.5)
def tick(t0, gain=0.03, pan=0.0): add(hp(noise(0.01), 2500) * np.linspace(1, 0, int(0.01 * SR)), t0, pan=pan, gain=gain, rev=0.1)
def zap(t0, gain=0.07, d=0.25):
    f = 2400 * np.exp(-tt(d) / 0.05) + 120
    v = np.sign(sine(f, d)) * 0.4 + sine(f * 1.5, d) * 0.4; v = v * np.exp(-tt(d) / 0.07)
    v = np.round(v * 6) / 6
    add(np.vstack([v, np.roll(v, 200)]), t0, gain=gain, rev=0.15)
def glitch_burst(t0, d=0.3, gain=0.05):
    r = np.random.default_rng(int(t0 * 100)); n = int(d * SR); v = np.zeros(n); i = 0
    while i < n:
        l = int(r.uniform(0.005, 0.03) * SR); kind = r.integers(0, 3)
        seg = noise(l / SR + 0.001)[:l] if kind == 0 else sine(r.uniform(300, 4000), l / SR + 0.001)[:l] if kind == 1 else np.zeros(l)
        seg = seg[:n - i]; v[i:i + len(seg)] = seg; i += l
    add(np.vstack([v, np.roll(v, 150)]), t0, gain=gain)
def agent_tone(t0, i, gain=0.06):
    notes = [(81, 88), (76, 83), (72, 79), (69, 76)][i]; d = 0.9
    v = sum(sine(midi(n), d) * np.exp(-tt(d) / 0.25) * (0.7 if j else 1) for j, n in enumerate(notes))
    add(np.vstack([v, np.roll(v, 900)]), t0, gain=gain, rev=0.5)

# -- intro / logo
riser(2.4, 4.0, 0.10, 150, 1600); reverse_swell(4.0, 1.4, 0.07)
for i in range(3): whoosh(3.05 + i * 0.12, 0.8, 300, 5000, 0.07, -0.7 + i * 0.7, 0.7 - i * 0.7)
impact(4.0, 0.55, 1.2); braam(4.0, 0.30); glint(5.0, 0.05); zap(5.05, 0.06); glitch_burst(5.06, 0.25, 0.03)
whoosh(6.9, 1.1, 300, 5000, 0.12); riser(6.6, 8.0, 0.11, 180, 2200)
impact(8.0, 0.5, 1.0)
# -- exploded UI
for i in range(9): tick(8.9 + i * 0.16, 0.035, -0.6 + i * 0.15)
for i in range(5): blip(9.7 + i * 0.12, 1568 + i * 120, 0.03, 0.5)
whoosh(12.4, 0.9, 2500, 500, 0.06)
riser(13.0, 14.1, 0.10, 300, 3000); whoosh(13.3, 0.85, 200, 7000, 0.15, 0.0, 0.0)
impact(14.1, 0.5, 1.0); zap(14.12, 0.04)
# -- agents
for i, (a0, a1) in enumerate([(14.1, 16.45), (16.95, 18.95), (19.45, 21.45), (21.95, 23.4)]):
    agent_tone(max(a0, 14.9) if i == 0 else a0 + 0.05, i, 0.07)
for tr in (16.45, 18.95, 21.45): whoosh(tr - 0.05, 0.6, 500, 3500, 0.10, 0.7, -0.7)
whoosh(23.35, 0.75, 300, 8000, 0.16, 0.8, -0.8); riser(23.2, 24.05, 0.09, 300, 3000)
impact(24.05, 0.5, 1.0); glitch_burst(24.0, 0.25, 0.06)
# -- chart flythrough
whoosh(24.1, 1.6, 200, 1500, 0.06, -0.5, 0.5)
for k in range(10): tick(24.6 + k * 0.42, 0.025, 0.5 - k * 0.1)
whoosh(28.5, 1.6, 2500, 300, 0.09, 0.6, -0.6)
glitch_burst(30.05, 0.3, 0.06); zap(30.1, 0.05)
impact(30.2, 0.38, 0.8)
# -- alerts
for k in range(8): blip(30.45 + k * 0.5, 1319 if k % 2 == 0 else 1175, 0.07, 0.4 * (1 if k % 2 else -1), two=True)
whoosh(34.5, 0.7, 4000, 500, 0.10); tick(35.15, 0.06)
impact(35.25, 0.3, 0.7)
# -- video wall
for k in range(5): whoosh(35.3 + abs(k - 2) * 0.12, 0.6, 600, 3000, 0.04, -0.8 + k * 0.4, -0.8 + k * 0.4)
riser(37.4, 40.0, 0.14, 200, 3200); reverse_swell(40.0, 1.5, 0.10); whoosh(38.8, 1.25, 4000, 200, 0.12)
impact(40.0, 0.62, 1.4); braam(40.0, 0.26); glint(40.02, 0.06)
# -- telegram
whoosh(40.05, 1.4, 400, 5000, 0.10, -0.6, 0.6); glint(41.6, 0.05)
whoosh(41.0, 2.4, 600, 3500, 0.07, -0.9, 0.9, q=3.0)
whoosh(44.5, 1.1, 400, 3500, 0.09, 0.6, -0.4)
impact(45.0, 0.25, 0.7)
whoosh(46.25, 0.8, 600, 6000, 0.07, -0.5, 0.5); blip(46.65, 1760, 0.05, 0.0, two=True)
glint(47.5, 0.05); glint(49.95, 0.04)
# -- closing
impact(50.0, 0.55, 1.5)
fin = np.zeros(int(2.2 * SR))
for n in (45, 57, 60, 64, 69, 76): fin += (saw(midi(n), 2.2) * 0.25 + sine(midi(n), 2.2)) * np.exp(-tt(2.2) / 0.8)
fin = lp(fin, 2600) * adsr(2.2, 0.005, 0.1, 1.0, 1.0)
add(np.vstack([fin, np.roll(fin, 400)]), 50.0, gain=0.05, rev=0.6); glint(50.02, 0.07)

# ------------------------------------------------------------------ mix
def reverb_ir(d=2.6, seed=1):
    r = np.random.default_rng(seed); n = int(d * SR)
    ir = r.standard_normal(n) * np.exp(-np.arange(n) / (0.55 * SR)); ir = lp(ir, 6000); ir[:int(0.012 * SR)] = 0
    return ir / np.sqrt(np.sum(ir ** 2))
rl = signal.fftconvolve(RVL, reverb_ir(seed=1))[:N] * 0.55; rr = signal.fftconvolve(RVR, reverb_ir(seed=2))[:N] * 0.55
mix = np.vstack([L + MUSIC_L * DUCK + DRUMS_L + rl, R + MUSIC_R * DUCK + DRUMS_R + rr])
mix = hp(mix, 28)
mix[:, :idx(0.05)] *= np.linspace(0, 1, idx(0.05))
mix[:, idx(51.2):] *= np.linspace(1, 0, N - idx(51.2)) ** 1.5
pk = np.max(np.abs(mix)); mix = np.tanh(mix / pk * 1.25) * 0.89
wavfile.write(sys.argv[1], SR, (mix.T * 32767).astype(np.int16))
print("peak", np.max(np.abs(mix)), "rms", np.sqrt(np.mean(mix ** 2)))
