import sys, json, numpy as np, soundfile as sf
from kokoro_onnx import Kokoro
K = Kokoro("tts/model.onnx", "tts/voices.bin")
VOICE = sys.argv[1] if len(sys.argv) > 1 else "af_heart"
LINES = [
 ("The market never sleeps.", "The market never sleeps."),
 ("Meet GrokBot.", "Meet Grok Bot."),
 ("Your whole trading desk, in one app.", "Your whole trading desk, in one app."),
 ("Balance, chart, agents and activity — live.", "Balance, chart, agents and activity. Live."),
 ("The Scanner hunts for new pairs.", "The Scanner hunts for new pairs."),
 ("The Analyst scores every signal.", "The Analyst scores every signal."),
 ("The Executor places the trades.", "The Executor places the trades."),
 ("Risk manages the stop-loss.", "Risk manages the stop loss."),
 ("Every position, tracked from entry to exit.", "Every position, tracked from entry to exit."),
 ("Every win — and every stop-loss — on the record.", "Every win, and every stop loss, on the record."),
 ("Every alert, straight to your phone.", "Every alert, straight to your phone."),
 ("Profit taken or stop triggered — you’ll know.", "Profit taken, or stop triggered. You'll know."),
 ("One dashboard for all of it.", "One dashboard, for all of it."),
 ("GrokBot is now on Telegram.", "Grok Bot is now on Telegram."),
 ("Join at t.me/grokbotting", "Join at t dot me, slash grok botting."),
]
out = []
for i, (sub, say) in enumerate(LINES):
    a, sr = K.create(say, voice=VOICE, speed=1.0, lang="en-us")
    # trim leading/trailing silence
    nz = np.where(np.abs(a) > 0.01)[0]; a = a[max(0, nz[0] - 240): nz[-1] + 1200]
    sf.write(f"tts/{VOICE}_{i:02d}.wav", a, sr)
    out.append(dict(i=i, sub=sub, say=say, dur=len(a) / sr, sr=sr))
json.dump(out, open(f"tts/{VOICE}_lines.json", "w"), indent=1)
for o in out: print("%2d %.2fs  %s" % (o["i"], o["dur"], o["say"]))
