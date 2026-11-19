#!/usr/bin/env python3
"""Preview V-Log LUTs on an ordinary photo: one JPEG per .cube, plus the V-Log input and the original.

  vlog_preview.py PHOTO.jpg LUT_DIR OUT_DIR

Pipeline: sRGB photo -> linear Rec.709 -> V-Gamut -> V-Log OETF -> .cube (trilinear) -> JPEG, with a 24-patch
colour strip under the picture. Approximates what the camera shows when a V-Log LUT runs as a Real Time LUT.
Used to build the proof sheet that chose which 21 of 41 pack LUTs went into the S9's library.
"""
import glob, os, sys
import numpy as np
from PIL import Image

OUT, CARD = None, None  # set from the command line
W = 480

def xyz_matrix(prims, white=(0.3127, 0.3290)):
    def xyz(xy): x, y = xy; return np.array([x / y, 1.0, (1 - x - y) / y])
    P = np.stack([xyz(p) for p in prims], axis=1)
    S = np.linalg.solve(P, xyz(white))
    return P * S

R709 = xyz_matrix([(0.64, 0.33), (0.30, 0.60), (0.15, 0.06)])
VGAM = xyz_matrix([(0.730, 0.280), (0.165, 0.840), (0.100, -0.030)])
M709_TO_VG = np.linalg.solve(VGAM, R709)

def srgb_to_linear(c):
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)

def vlog(x):
    x = np.maximum(x, 0)
    return np.where(x < 0.01, 5.6 * x + 0.125, 0.241514 * np.log10(x + 0.00873) + 0.598206)

def load_cube(path):
    n, lo, hi, vals = None, np.zeros(3), np.ones(3), []
    for line in open(path, encoding="utf-8", errors="ignore"):
        s = line.strip()
        if not s or s.startswith("#"): continue
        t = s.split()
        k = t[0].upper()
        if k == "LUT_3D_SIZE": n = int(t[1])
        elif k == "DOMAIN_MIN": lo = np.array(list(map(float, t[1:4])))
        elif k == "DOMAIN_MAX": hi = np.array(list(map(float, t[1:4])))
        elif k[0].isdigit() or k[0] in "-.":
            vals.append(list(map(float, t[:3])))
    lut = np.array(vals, dtype=np.float32).reshape(n, n, n, 3)  # [b][g][r]
    return lut, n, lo, hi

def apply_cube(img, lut, n, lo, hi):
    x = np.clip((img - lo) / (hi - lo), 0, 1) * (n - 1)
    i0 = np.floor(x).astype(int); i1 = np.minimum(i0 + 1, n - 1); f = x - i0
    r0, g0, b0 = i0[..., 0], i0[..., 1], i0[..., 2]
    r1, g1, b1 = i1[..., 0], i1[..., 1], i1[..., 2]
    fr, fg, fb = f[..., 0:1], f[..., 1:2], f[..., 2:3]
    c = lambda b, g, r: lut[b, g, r]
    c00 = c(b0, g0, r0) * (1 - fr) + c(b0, g0, r1) * fr
    c01 = c(b0, g1, r0) * (1 - fr) + c(b0, g1, r1) * fr
    c10 = c(b1, g0, r0) * (1 - fr) + c(b1, g0, r1) * fr
    c11 = c(b1, g1, r0) * (1 - fr) + c(b1, g1, r1) * fr
    c0 = c00 * (1 - fg) + c01 * fg
    c1 = c10 * (1 - fg) + c11 * fg
    return c0 * (1 - fb) + c1 * fb

CHECKER = [(115,82,68),(194,150,130),(98,122,157),(87,108,67),(133,128,177),(103,189,170),
           (214,126,44),(80,91,166),(193,90,99),(94,60,108),(157,188,64),(224,163,46),
           (56,61,150),(70,148,73),(175,54,60),(231,199,31),(187,86,149),(8,133,161),
           (243,243,242),(200,200,200),(160,160,160),(122,122,121),(85,85,85),(52,52,52)]

def build_sample(photo):
    im = Image.open(photo).convert("RGB")
    w, h = im.size; th = int(w * 9 / 16)
    top = (h - th) // 2 if h > th else 0
    im = im.crop((0, top, w, top + min(th, h))).resize((W, int(W * 9 / 16)), Image.LANCZOS)
    sw = W // 12; strip = Image.new("RGB", (W, sw * 2))
    for k, col in enumerate(CHECKER):
        strip.paste(col, ((k % 12) * sw, (k // 12) * sw, (k % 12 + 1) * sw, (k // 12 + 1) * sw))
    out = Image.new("RGB", (W, im.size[1] + strip.size[1]))
    out.paste(im, (0, 0)); out.paste(strip, (0, im.size[1]))
    return out

def main(photo):
    sample = build_sample(photo)
    sample.save(os.path.join(OUT, "_ORIGINAL.jpg"), quality=88)
    a = np.asarray(sample).astype(np.float32) / 255.0
    lin = srgb_to_linear(a)
    vg = np.einsum("ij,hwj->hwi", M709_TO_VG, lin)
    log = vlog(vg).astype(np.float32)
    Image.fromarray((np.clip(log, 0, 1) * 255).astype(np.uint8)).save(os.path.join(OUT, "_VLOG_INPUT.jpg"), quality=88)
    for p in sorted(glob.glob(os.path.join(CARD, "*.cube"))):
        lut, n, lo, hi = load_cube(p)
        res = np.clip(apply_cube(log, lut, n, lo, hi), 0, 1)
        name = os.path.splitext(os.path.basename(p))[0]
        Image.fromarray((res * 255 + 0.5).astype(np.uint8)).save(os.path.join(OUT, name + ".jpg"), quality=85)
        print("ok", name, n)

if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    CARD, OUT = sys.argv[2], sys.argv[3]
    os.makedirs(OUT, exist_ok=True)
    main(sys.argv[1])
