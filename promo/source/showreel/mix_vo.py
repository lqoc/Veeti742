import numpy as np, soundfile as sf
from scipy import signal
SR = 48000
score, sr1 = sf.read("score2.wav"); vo, sr2 = sf.read("vo_track.wav"); assert sr1 == sr2 == SR
n = min(len(score), len(vo)); score = score[:n]; vo = vo[:n]
sos = lambda kind, f, o=2: signal.butter(o, f, kind, fs=SR, output="sos")
# --- voice chain: HPF, presence lift, compressor
v = signal.sosfilt(sos("high", 85), vo)
v = v + 0.35 * signal.sosfilt(sos("band", [2500, 6000]), v) - 0.15 * signal.sosfilt(sos("band", [180, 400]), v)
def follow(x, att, rel):
    e = np.abs(x); out = np.zeros_like(e); a_ = np.exp(-1 / (att * SR)); r_ = np.exp(-1 / (rel * SR)); s = 0.0
    # vectorised-ish: process in blocks of 64 for speed
    for i in range(0, len(e), 64):
        m = e[i:i + 64].max(); s = a_ * s + (1 - a_) * m if m > s else r_ * s + (1 - r_) * m
        out[i:i + 64] = s
    return out
env = follow(v, 0.005, 0.12)
db = 20 * np.log10(env + 1e-9); thr = -22.0; ratio = 3.0
gr = np.where(db > thr, (db - thr) * (1 - 1 / ratio), 0.0)
v = v * 10 ** (-gr / 20)
speech = np.abs(v) > 0.005
rms = np.sqrt(np.mean(v[speech] ** 2)); v = v * (10 ** (-17 / 20) / rms)          # speech RMS at -17 dBFS
# small room
ir_n = int(0.5 * SR); r = np.random.default_rng(4); ir = r.standard_normal(ir_n) * np.exp(-np.arange(ir_n) / (0.09 * SR))
ir = signal.sosfilt(sos("low", 5000), ir); ir /= np.sqrt(np.sum(ir ** 2))
wet = signal.fftconvolve(v, ir)[:n] * 0.07
vs = np.stack([v + wet, v + np.roll(wet, 240)], 1)
# --- ducking of the score under speech
act = follow(v, 0.04, 0.45); act = np.clip(act / (act.max() * 0.15), 0, 1)
duck = 1 - 0.5 * act                                                                  # about -6 dB under speech
mix = score * duck[:, None] + vs
sf.write("mix_vo.wav", mix, SR, subtype="FLOAT")
print("duck min %.2f  vo peak %.2f  mix peak %.2f" % (duck.min(), np.abs(vs).max(), np.abs(mix).max()))
