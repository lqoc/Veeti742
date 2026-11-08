# GrokBot promo video

`GrokBot_Promo_1080p60.mp4` — 42 s, 1920×1080, 60 fps, H.264 High, AAC 320 kbps (-13.7 LUFS, -1.2 dBTP).

Storyboard: cinematic logo reveal → "Meet GrokBot." → logo morphs into the phone → 01 Live dashboard →
02 Agents → 03 Alerts → 04 Positions → "Your trading desk. Reimagined." → zoom through the in-app
Telegram badge → Telegram → end card with **t.me/grokbotting**.

All product shots are the original GrokBot screen recordings (cropped and framed, never altered).
Profit figures in that footage are labelled on screen as demo / illustrative, and the end card carries a
risk line. Music and sound effects are synthesised from scratch in `source/audio.py` (no samples, no licensing).

## Rebuild
`source/ad.py` is a deterministic Skia renderer (`frame(t)`), `source/render.py` renders it in parallel,
`source/audio.py out.wav` synthesises the score, `source/assemble.sh` encodes the final MP4.
Needs `pip install skia-python scipy pillow numpy`, ffmpeg, Inter fonts, and the source clips extracted
to `src/A..D` at constant frame rate (60/60/30/30 fps).

---

# GrokBot showreel (v2, with voice-over)

`GrokBot_Showreel_VO_1080p60.mp4` — 52 s, 1920×1080, 60 fps, H.264 + AAC 320 kbps (-14.3 LUFS). Narrated voice-over
with subtitles synced to it word by word (burned in, plus a selectable English track); `GrokBot_Showreel.en.srt` is the
same captions as a sidecar file.

Voice: Kokoro-82M (Apache-2.0, open weights, run locally), voice `af_heart`. Music and effects duck about 6 dB under
the narration. To change voice, run `source/showreel/vo.py <voice>` and `vo_final.py`, then `mix_vo.py` and re-render.

Shots: 3D kinetic type over a grid flythrough → extruded 3D logo forge with god rays and a glitch wordmark →
card-flip morph into the phone → exploded 3D UI stack with callouts → agent carousel ring (Scanner, Analyst,
Executor, Risk) with pipeline particles and reflections → 3D equity-ribbon flythrough with win/stop-loss pins →
3D notification deck → curved video wall of the original screen recordings → extruded Telegram disc with a
paper-plane light trail → call to action **t.me/grokbotting**.

The app UI is a vector re-implementation of `grokbot-mobile-trading-clean` (same layout, colours and ledger
logic), so it stays sharp in 3D. That tool generates a fictional ledger, so every on-screen figure is labelled
"Illustrative data · Not financial advice" and the captions describe features, not returns.
Source: `source/showreel/` (`reel.py` shots, `core3d.py` camera/UI/ledger, `audio2.py` score).
