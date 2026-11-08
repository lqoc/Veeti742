import json, numpy as np, soundfile as sf
from kokoro_onnx import Kokoro
K = Kokoro("tts/model.onnx", "tts/voices.bin"); VOICE = "af_heart"
L = json.load(open("tts/af_heart_lines.json"))
# (start time, speed) per line, placed against the picture cuts
PLAN = [(1.0, 1.0), (5.25, 1.0), (8.45, 1.0), (11.05, 1.08), (14.6, 1.0), (17.0, 1.0), (19.5, 1.0), (22.0, 1.0),
        (24.5, 1.0), (27.25, 1.0), (30.6, 1.0), (33.05, 1.12), (35.8, 1.0), (41.3, 1.0), (46.6, 1.0)]
SR = 48000; N = int(52.0 * SR); vo = np.zeros(N); timing = []
from scipy.signal import resample_poly
for (t0, sp), o in zip(PLAN, L):
    a, sr = K.create(o["say"], voice=VOICE, speed=sp, lang="en-us")
    nz = np.where(np.abs(a) > 0.01)[0]; a = a[max(0, nz[0] - 240): nz[-1] + 1200]
    a = resample_poly(a, SR, sr)
    i0 = int(t0 * SR); vo[i0:i0 + len(a)] += a[:N - i0]
    dur = len(a) / SR
    # word reveal times: proportional to spoken-text character position (sub words mapped onto that span)
    words = o["sub"].split(" "); tot = len(o["sub"]); acc = 0; wt = []
    for w in words: wt.append(round(t0 + dur * 0.92 * acc / tot, 3)); acc += len(w) + 1
    timing.append(dict(t0=t0, t1=round(t0 + dur, 3), text=o["sub"], words=wt))
    print("%5.2f-%5.2f  %s" % (t0, t0 + dur, o["sub"]))
sf.write("vo_track.wav", vo, SR, subtype="PCM_24")
json.dump(timing, open("subs_timing.json", "w"), indent=1)
