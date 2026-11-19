#!/usr/bin/env python3
"""Load a whole set of LUTs into the camera's LUT Library over USB, from a manifest (see lumix/batch.py).

  lut_batch.py plan  MANIFEST [--base=VLOG|STD|NAT|keep] [--replace]          read only: what would go where
  lut_batch.py load  MANIFEST --yes [--base=...] [--replace] [--backup=DIR]   upload + verify each LUT
  lut_batch.py list                                                            read only: the library, parsed

MANIFEST lines: path,name[,position]   e.g.  luts/SubtleContrast1.cube,FE Subtle Contrast 1
--base defaults to VLOG (no #LUMIXPHOTOSTYLE tag, applied on V-Log); 'keep' keeps each file's own tag.
--backup downloads every user LUT already on the camera into DIR before the first write.
Camera: USB mode LUMIX Lab or PC (Tether); quit the LUMIX Lab app first so it doesn't hold the camera.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from lumix import batch, luts  # noqa: E402
from lumix.ptp import Camera, CameraError  # noqa: E402


def opt(args, key, default=None):
    for a in args:
        if a.startswith(f'--{key}='):
            return a.split('=', 1)[1]
    return default


def show_library(lib):
    for e in lib:
        b = e['base_style']  # style name, None (V-Log default) or a raw code the table doesn't know
        base = 'V-Log default' if b is None else b if isinstance(b, str) else f'style {b}'
        print(f"  {e['position']:2d}  {str(e['type']):8s}  {base:13s}  {e['name']}")
    print(f'  {len(lib)} entries, {sum(1 for p in range(2, 41) if p not in {e["position"] for e in lib})} free positions (2-40)')


def main(argv):
    if len(argv) < 1 or argv[0] not in ('plan', 'load', 'list'):
        sys.exit(__doc__)
    cmd, args = argv[0], argv[1:]
    base = opt(args, 'base', 'VLOG')
    base = None if base.lower() == 'keep' else base
    replace = '--replace' in args
    try:
        with Camera() as cam:
            print(f'Camera USB mode: {cam.usb_mode}')
            lib = luts.library(cam)
            if cmd == 'list':
                show_library(lib)
                return 0
            items = batch.read_manifest(args[0])
            steps = batch.plan(lib, items, replace)
            for s in steps:
                batch.prepare(s['path'], s['name'], base)  # every file must be valid before anything is written
            print(batch.format_plan(steps, lib), end='')
            print(f"Base Photo Style: {'each file keeps its own tag' if base is None else base}")
            if cmd == 'plan':
                print('Plan only, nothing written. Run "load ... --yes" to upload.')
                return 0
            if '--yes' not in args:
                print('Add --yes to upload.')
                return 1
            backup_dir = opt(args, 'backup')
            if backup_dir:
                saved = luts.backup(cam, os.path.expanduser(backup_dir))
                print(f'Backed up {len(saved)} user LUT(s) to {backup_dir}')
            results = batch.load(cam, items, base, replace)
            print(f'Done: {sum(r["ok"] for r in results)}/{len(results)} uploaded and verified.')
            show_library(luts.library(cam))
            return 0
    except CameraError as e:
        print(f'error: {e}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
