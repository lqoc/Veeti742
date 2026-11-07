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
