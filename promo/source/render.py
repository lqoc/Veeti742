import sys, os, subprocess, time
from multiprocessing import Pool
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
OUT = os.path.join(HERE, "chunks"); os.makedirs(OUT, exist_ok=True)
FPS = 60; NF = int(42.0 * FPS); CH = 60
def job(ci):
    import ad
    f0, f1 = ci * CH, min(NF, (ci + 1) * CH)
    path = os.path.join(OUT, "c%03d.mkv" % ci)
    if os.path.exists(path + ".done"): return ci, 0
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "1920x1080", "-r", str(FPS), "-i", "-",
                          "-c:v", "libx264", "-preset", "veryfast", "-crf", "6", "-pix_fmt", "yuv444p", path], stdin=subprocess.PIPE)
    t0 = time.time()
    for i in range(f0, f1):
        p.stdin.write(ad.frame(i / FPS, i).tobytes())
    p.stdin.close(); p.wait(); open(path + ".done", "w").close()
    return ci, time.time() - t0
if __name__ == "__main__":
    n = (NF + CH - 1) // CH
    order = sorted(range(n), key=lambda c: -(1 if 29 <= c * CH / FPS <= 33 else 0))  # heavy chunks first
    t0 = time.time()
    with Pool(4) as pool:
        for k, (ci, dt) in enumerate(pool.imap_unordered(job, order)):
            print(f"chunk {ci} done in {dt:.0f}s  [{k+1}/{n}] elapsed {time.time()-t0:.0f}s", flush=True)
