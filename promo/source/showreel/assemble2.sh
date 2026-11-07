#!/bin/bash
set -e
cd "$(dirname "$0")"
ls chunks2/c*.mkv | sort | sed "s#^#file '$PWD/#; s#\$#'#" > chunks2.txt
ffmpeg -v error -y -f concat -safe 0 -i chunks2.txt -i score2_m.wav -i GrokBot_Showreel.en.srt \
  -map 0:v -map 1:a -map 2:s -c:v libx264 -preset slow -crf 17 -profile:v high -level 4.2 -pix_fmt yuv420p \
  -x264-params "keyint=120:min-keyint=60:aq-mode=3:aq-strength=0.9:deblock=-1,-1" \
  -colorspace bt709 -color_primaries bt709 -color_trc bt709 -color_range tv -vf "scale=out_color_matrix=bt709:out_range=tv" -r 60 \
  -c:a aac -b:a 320k -ar 48000 -c:s mov_text -metadata:s:s:0 language=eng -movflags +faststart -t 52.0 GrokBot_Showreel_1080p60.mp4
ffprobe -v error -show_entries stream=codec_type,codec_name,width,height,r_frame_rate,bit_rate:format=duration,size -of compact GrokBot_Showreel_1080p60.mp4
