import sys, os, subprocess, time
from multiprocessing import Pool
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
OUT = os.path.join(HERE, "chunks2"); os.makedirs(OUT, exist_ok=True)
FPS = 60; NF = int(52.0 * FPS); CH = 60
def cost(ci):
    import reel
    s = 0
    for f in range(ci * CH, min(NF, (ci + 1) * CH)):
        t = f / FPS; n = 1
        for (a, b), k in reel.MB:
            if a <= t <= b: n = max(n, k)
        s += n
    return s
def job(ci):
    import reel
    f0, f1 = ci * CH, min(NF, (ci + 1) * CH)
    path = os.path.join(OUT, "c%03d.mkv" % ci)
    if os.path.exists(path + ".done"): return ci, 0
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "1920x1080", "-r", str(FPS), "-i", "-",
                          "-c:v", "libx264", "-preset", "veryfast", "-crf", "6", "-pix_fmt", "yuv444p", path], stdin=subprocess.PIPE)
    t0 = time.time()
    for i in range(f0, f1): p.stdin.write(reel.frame(i / FPS, i).tobytes())
    p.stdin.close(); p.wait(); open(path + ".done", "w").close()
    return ci, time.time() - t0
if __name__ == "__main__":
    n = (NF + CH - 1) // CH
    order = sorted(range(n), key=lambda c: -cost(c))
    t0 = time.time()
    with Pool(4) as pool:
        for k, (ci, dt) in enumerate(pool.imap_unordered(job, order)):
            print(f"chunk {ci} done in {dt:.0f}s  [{k+1}/{n}] elapsed {time.time()-t0:.0f}s", flush=True)
