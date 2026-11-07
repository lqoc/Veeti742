#!/bin/bash
set -e
S=$(dirname "$0"); cd "$S"
ls chunks/c*.mkv | sort | sed "s#^#file '$PWD/#; s#\$#'#" > chunks.txt
ffmpeg -v error -y -f concat -safe 0 -i chunks.txt -i score_m.wav \
  -map 0:v -map 1:a -c:v libx264 -preset slow -crf 16 -profile:v high -level 4.2 -pix_fmt yuv420p \
  -x264-params "keyint=120:min-keyint=60:aq-mode=3:aq-strength=0.9:deblock=-1,-1" \
  -colorspace bt709 -color_primaries bt709 -color_trc bt709 -color_range tv \
  -vf "scale=out_color_matrix=bt709:out_range=tv" -r 60 \
  -c:a aac -b:a 320k -ar 48000 -movflags +faststart -t 42.0 GrokBot_Promo_1080p60.mp4
ffprobe -v error -show_entries stream=codec_name,profile,width,height,r_frame_rate,pix_fmt,bit_rate,duration:format=duration,size,bit_rate -of compact GrokBot_Promo_1080p60.mp4
