"""Original score + sound design for the GrokBot promo (all synthesised, no samples). 48 kHz stereo WAV."""
import numpy as np, sys
from scipy import signal
from scipy.io import wavfile

SR = 48000; DUR = 42.0; N = int(SR * DUR)
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

# ------------------------------------------------------------------ harmony
# Am  F  C  G (per bar)
CHORDS = [[57, 60, 64, 69], [53, 57, 60, 65], [55, 60, 64, 67], [55, 59, 62, 67]]
ROOTS = [33, 29, 36, 31]
def chord_at(t): return int(t // BAR) % 4

MUSIC_L = np.zeros(N); MUSIC_R = np.zeros(N)
MB = (MUSIC_L, MUSIC_R)
DRUMS_L = np.zeros(N); DRUMS_R = np.zeros(N)
DB = (DRUMS_L, DRUMS_R)

def in_beat(t): return (8.0 <= t < 27.5) or (32.0 <= t < 40.0)

# ---- pad (supersaw, filter automation)
pad_cut = curve([(0, 260), (4, 700), (7.9, 1900), (8, 2600), (27.5, 2600), (28, 900), (31.9, 3400), (32, 3200), (40, 3600), (42, 1200)], N)
pad = np.zeros((2, N))
for b in range(int(DUR // BAR) + 1):
    t0 = b * BAR
    if t0 >= 40.0: break
    ch = CHORDS[b % 4]
    d = BAR + 0.9 if t0 + BAR < 40 else 2.0 + 2.0
    if t0 + BAR >= 40: ch = CHORDS[0]
    for k, n in enumerate(ch):
        for j, det in enumerate((-0.11, 0.0, 0.12)):
            v = saw(midi(n) * 2 ** (det / 12), d, rng.uniform()) * adsr(d, 0.45, 0.4, 0.8, 0.9 if t0 + BAR < 40 else 3.0)
            pan = (-0.6, 0.0, 0.6)[j] * (0.5 + 0.5 * (k % 2))
            i0 = idx(t0); seg = v[:N - i0]
            pad[0, i0:i0 + len(seg)] += seg * np.cos((pan + 1) * np.pi / 4)
            pad[1, i0:i0 + len(seg)] += seg * np.sin((pan + 1) * np.pi / 4)
# final chord at 40 sustains (rendered separately below); trim pad after 40
pad[:, idx(40.0):] *= np.linspace(1, 0, N - idx(40.0)) ** 3
for c in range(2): pad[c] = sweep_lp(pad[c], pad_cut, q=0.8)
pad_vol = curve([(0, 0), (1.5, 0.05), (4, 0.065), (8, 0.06), (27.5, 0.06), (29, 0.075), (32, 0.06), (40, 0.06), (42, 0.02)], N)
add(pad * pad_vol * 0.9, 0, bus=MB, rev=0.0)
RVL += pad[0] * pad_vol * 0.35; RVR += pad[1] * pad_vol * 0.35

# ---- intro drone + air
dr = (sine(midi(33), 8.5) * 0.5 + sine(midi(45), 8.5) * 0.25 + lp(noise(8.5), 400) * 0.08) * curve([(0, 0), (1.2, 0.12), (6, 0.14), (8.3, 0)], int(8.5 * SR))
add(dr, 0, gain=0.9)
air = hp(noise(8.5), 3000) * curve([(0, 0), (1.5, 0.012), (7.5, 0.016), (8.2, 0)], int(8.5 * SR))
add(np.vstack([air, np.roll(air, 900)]), 0)

# ---- arp pluck (16ths) with ping-pong delay
arp = np.zeros((2, N))
pattern = [0, 1, 2, 3, 2, 1, 0, 2, 3, 2, 1, 3, 0, 1, 2, 3]
s16 = BEAT / 4
t = 4.0
while t < 40.0 - 1e-6:
    k = int(round(t / s16))
    ch = CHORDS[chord_at(t)]
    n = ch[pattern[k % 16]] + 12 + (12 if (k % 32) >= 28 else 0)
    d = 0.22
    tone = saw(midi(n), d) * 0.6 + sine(midi(n) * 2, d) * 0.25
    env_ = expdec(d, 0.055)
    fc = 900 + 4200 * expdec(d, 0.03)
    v = sweep_lp(tone, fc * (0.6 + 0.4 * min(1, max(0, (t - 4) / 4))), block=64, q=1.2) * env_
    vel = 0.75 + 0.25 * (k % 4 == 0)
    i0 = idx(t); seg = v[:N - i0] * vel
    pan = 0.25 * np.sin(k * 0.7)
    arp[0, i0:i0 + len(seg)] += seg * (0.7 - pan * 0.4); arp[1, i0:i0 + len(seg)] += seg * (0.7 + pan * 0.4)
    t += s16
dl = int(0.375 * SR)
d1 = np.zeros_like(arp)
d1[1, dl:] += arp[0, :-dl] * 0.38; d1[0, 2 * dl:] += arp[1, :-2 * dl] * 0.25
arp = arp + lp(d1, 3500)
arp_vol = curve([(4, 0.0), (6, 0.05), (7.9, 0.09), (8, 0.085), (27.5, 0.085), (28, 0.11), (31.8, 0.12), (32, 0.095), (40, 0.095)], N)
add(arp * arp_vol, 0, bus=MB)
RVL += arp[0] * arp_vol * 0.25; RVR += arp[1] * arp_vol * 0.25

# ---- bass (8ths, sidechained)
t = 8.0
while t < 40.0 - 1e-6:
    if in_beat(t):
        k = int(round(t / (BEAT / 2)))
        root = ROOTS[chord_at(t)] + (12 if k % 2 else 0)
        d = BEAT / 2 * 0.92
        v = saw(midi(root), d) * 0.7 + sine(midi(root), d) * 0.6
        v = lp(v, 520 + 300 * (t > 32)) * adsr(d, 0.004, 0.08, 0.7, 0.03)
        add(v, t, gain=0.20, bus=MB)
    t += BEAT / 2

# ---- drums
def kick():
    d = 0.5; f = 46 + 110 * np.exp(-tt(d) / 0.035)
    v = sine(f, d) * np.exp(-tt(d) / 0.18)
    click = hp(noise(0.012), 2000) * np.linspace(1, 0, int(0.012 * SR)) * 0.25
    v[:len(click)] += click
    return np.tanh(v * 1.6)
def clap():
    d = 0.3; v = np.zeros(int(d * SR))
    for o in (0, 0.009, 0.018):
        i = int(o * SR); n_ = bp(noise(d - o), 1100, 5200) * np.exp(-tt(d - o) / (0.012 if o < 0.018 else 0.09))
        v[i:i + len(n_)] += n_[:len(v) - i]
    return v
def hat(open_=False):
    d = 0.25 if open_ else 0.06
    return hp(noise(d), 7000, 4) * np.exp(-tt(d) / (0.09 if open_ else 0.018))
K, C_, HH = kick(), clap(), hat()
t = 8.0
while t < 40.0 - 1e-6:
    if in_beat(t):
        b = int(round(t / BEAT))
        add(K, t, gain=0.62, bus=DB)
        i0 = idx(t); n_ = idx(0.42)
        dk = 1 - 0.68 * np.exp(-np.arange(n_) / (0.11 * SR)) * (1 - np.exp(-np.arange(n_) / (0.004 * SR)))
        DUCK[i0:i0 + n_] = np.minimum(DUCK[i0:i0 + n_], dk[:N - i0])
        if b % 2 == 1: add(C_, t, gain=0.20, bus=DB, rev=0.25)
        add(HH, t + BEAT / 2, gain=0.085, pan=0.25, bus=DB)
        if t >= 32:
            add(HH, t + BEAT / 4, gain=0.035, pan=-0.3, bus=DB); add(HH, t + 3 * BEAT / 4, gain=0.035, pan=-0.3, bus=DB)
    t += BEAT
# breakdown: snare build 31.0-32.0
for i in range(16):
    tb = 31.0 + i * (1.0 / 16)
    add(C_, tb, gain=0.03 + 0.10 * (i / 15) ** 2, bus=DB, rev=0.2)
# fill into 8.0 and 36.0 (crash-ish open hats)
for tc in (8.0, 32.0, 36.0):
    add(hp(noise(1.6), 5000, 2) * np.exp(-tt(1.6) / 0.45), tc, gain=0.06, rev=0.4, bus=DB)

# ------------------------------------------------------------------ SFX
def whoosh(t0, d, f0=300, f1=4000, gain=0.18, pan0=-0.6, pan1=0.6, q=1.6):
    n = int(d * SR)
    x = noise(d)
    fc = np.exp(np.linspace(np.log(f0), np.log(f1), n))
    y = sweep_bp(x, fc, q=q) * np.sin(np.linspace(0, np.pi, n)) ** 1.6
    pan = np.linspace(pan0, pan1, n)
    st = np.vstack([y * np.cos((pan + 1) * np.pi / 4), y * np.sin((pan + 1) * np.pi / 4)]) * 1.414
    add(st, t0, gain=gain, rev=0.25)
def impact(t0, gain=0.5, size=1.0):
    d = 2.2 * size
    f = 32 + 55 * np.exp(-tt(d) / 0.12)
    boom = sine(f, d) * np.exp(-tt(d) / (0.55 * size))
    hit = lp(noise(d), 1800) * np.exp(-tt(d) / 0.06) * 0.6
    add(np.tanh((boom + hit) * 1.3), t0, gain=gain, rev=0.35 * size)
def riser(t0, t1, gain=0.12, f0=200, f1=2400):
    d = t1 - t0; n = int(d * SR)
    x = noise(d); fc = np.exp(np.linspace(np.log(f0 * 2), np.log(f1 * 3), n))
    y = sweep_bp(x, fc, q=3.0) * np.linspace(0, 1, n) ** 2.2
    tone = saw(np.exp(np.linspace(np.log(f0), np.log(f1), n)), d) * np.linspace(0, 1, n) ** 3 * 0.15
    y = y + lp(tone, 5000)
    add(np.vstack([y, np.roll(y, 300)]), t0, gain=gain, rev=0.3)
def blip(t0, f=1320, gain=0.06, pan=0.3, two=False):
    d = 0.22
    v = (sine(f, d) + 0.3 * sine(f * 2.01, d)) * np.exp(-tt(d) / 0.04)
    add(v, t0, pan=pan, gain=gain, rev=0.25)
    if two:
        add((sine(f * 1.5, d) + 0.3 * sine(f * 3.0, d)) * np.exp(-tt(d) / 0.05), t0 + 0.085, pan=pan, gain=gain * 0.9, rev=0.3)
def glint(t0, gain=0.05):
    d = 1.2; v = np.zeros(int(d * SR))
    for i, f in enumerate((2637, 3520, 4186, 5274)):
        o = int(i * 0.035 * SR); vv = sine(f * (1 + 0.002 * i), d - i * 0.035) * np.exp(-tt(d - i * 0.035) / 0.25)
        v[o:o + len(vv)] += vv * (0.6 ** i)
    add(np.vstack([v, np.roll(v, 600)]), t0, gain=gain, rev=0.5)
def tick(t0, gain=0.03, pan=0.0):
    add(hp(noise(0.01), 2500) * np.linspace(1, 0, int(0.01 * SR)), t0, pan=pan, gain=gain, rev=0.1)

whoosh(0.35, 1.6, 200, 2600, 0.14, -0.8, 0.0); whoosh(0.4, 1.55, 220, 2800, 0.14, 0.8, 0.0)
riser(0.6, 1.95, 0.07, 150, 900)
impact(1.95, 0.55, 1.2); glint(1.97, 0.06)
glint(2.65, 0.045)
for i in range(7): tick(2.95 + i * 0.055, 0.025, -0.6 + i * 0.2)
whoosh(4.12, 0.95, 400, 2000, 0.06)
impact(4.55, 0.16, 0.5); impact(4.67, 0.10, 0.4)
blip(5.15, 1760, 0.03, 0.0)
whoosh(6.4, 0.7, 900, 3000, 0.07, 0.2, -0.2)
riser(6.5, 8.0, 0.13, 180, 2200); whoosh(7.2, 0.85, 300, 6000, 0.12, -0.3, 0.3)
impact(8.0, 0.6, 1.2)
blip(9.02, 1568, 0.05, 0.5); blip(9.1, 2093, 0.035, 0.5); glint(9.7, 0.035)
whoosh(12.7, 1.0, 250, 5000, 0.16, 0.8, -0.8)
blip(13.6, 1568, 0.05, -0.5); blip(13.95, 2093, 0.035, -0.5)
for i in range(4): tick(14.15 + i * 0.09, 0.03, -0.5 + i * 0.25)
tick(16.7, 0.04, -0.4); blip(16.72, 1175, 0.03, -0.4)
whoosh(17.65, 1.15, 300, 4000, 0.13, 0.7, -0.7)
blip(18.85, 1319, 0.07, 0.5, two=True)
blip(21.6, 1319, 0.07, 0.5, two=True)
whoosh(22.72, 0.75, 500, 7000, 0.14, -0.8, 0.8); glint(23.0, 0.035)
blip(23.25, 1568, 0.045, 0.3); blip(23.6, 1760, 0.045, 0.6)
whoosh(27.25, 0.9, 3000, 300, 0.10, 0.0, 0.0)
impact(27.95, 0.22, 0.7); glint(28.5, 0.04)
whoosh(29.4, 0.7, 2000, 600, 0.06)
riser(30.3, 32.0, 0.15, 200, 3000); whoosh(30.9, 1.1, 300, 8000, 0.16, -0.2, 0.2)
impact(32.0, 0.62, 1.3); glint(32.02, 0.06)
impact(32.55, 0.16, 0.5); glint(32.95, 0.04)
whoosh(35.25, 1.0, 400, 3500, 0.10, 0.6, -0.4)
impact(36.0, 0.34, 0.9)
whoosh(36.45, 0.8, 600, 6000, 0.07, -0.5, 0.5); blip(36.85, 1760, 0.05, 0.0, two=True)
glint(37.6, 0.05); glint(39.85, 0.04)
# closing hit: big chord + sub + shimmer
impact(40.0, 0.55, 1.5)
fin = np.zeros(int(2.2 * SR))
for n in (45, 57, 60, 64, 69, 76):
    fin += (saw(midi(n), 2.2) * 0.25 + sine(midi(n), 2.2)) * np.exp(-tt(2.2) / 0.8)
fin = lp(fin, 2600) * adsr(2.2, 0.005, 0.1, 1.0, 1.0)
add(np.vstack([fin, np.roll(fin, 400)]), 40.0, gain=0.05, rev=0.6)
glint(40.02, 0.07)

# ------------------------------------------------------------------ mix
def reverb_ir(d=2.6, seed=1):
    r = np.random.default_rng(seed); n = int(d * SR)
    ir = r.standard_normal(n) * np.exp(-np.arange(n) / (0.55 * SR))
    ir = lp(ir, 6000); ir[:int(0.012 * SR)] = 0
    return ir / np.sqrt(np.sum(ir ** 2))
IRL, IRR = reverb_ir(seed=1), reverb_ir(seed=2)
# sends from music and drums too (light)
rl = signal.fftconvolve(RVL, IRL)[:N] * 0.55; rr = signal.fftconvolve(RVR, IRR)[:N] * 0.55

music_l = MUSIC_L * DUCK; music_r = MUSIC_R * DUCK
mix_l = L + music_l + DRUMS_L + rl; mix_r = R + music_r + DRUMS_R + rr
mix = np.vstack([mix_l, mix_r])
mix = hp(mix, 28)
# fade in/out
mix[:, :idx(0.05)] *= np.linspace(0, 1, idx(0.05))
mix[:, idx(41.2):] *= np.linspace(1, 0, N - idx(41.2)) ** 1.5
pk = np.max(np.abs(mix)); mix = mix / pk * 1.25
mix = np.tanh(mix) * 0.89   # gentle saturation / limiting
wavfile.write(sys.argv[1], SR, (mix.T * 32767).astype(np.int16))
print("peak", np.max(np.abs(mix)), "rms", np.sqrt(np.mean(mix ** 2)))
