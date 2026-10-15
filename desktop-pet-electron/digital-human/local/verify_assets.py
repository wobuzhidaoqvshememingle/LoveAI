from PIL import Image
import os, sys, json

d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
out = {}
for f in ["master.png", "master_greenscreen.png"]:
    p = os.path.join(d, f)
    im = Image.open(p)
    out[f] = {"size": im.size, "mode": im.mode, "bytes": os.path.getsize(p)}
    print(f, im.size, im.mode, os.path.getsize(p))

with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "out", "assets_verify.json"), "w") as fh:
    json.dump(out, fh, indent=2)
