#!/usr/bin/env python3
"""Does the camera apply new LUT contents written into the slot that is currently active, without re-selecting it?

  lut_swap_test.py OUTDIR [--manifest=FILE] [--slot=N] [--yes]

--slot=N: you selected the LUT at position N on the camera yourself. Needed in USB mode LUMIX Lab, where gphoto2
can't take the identifying shot; the check then uses live view only (the red test LUT turns the picture red).

1. Reads the library and a live-view frame, then takes a test shot (gphoto2) and reads LUT1Name from the maker note
   to find the active slot. Nothing is written in this phase.
2. Saves that slot's current contents (LutGetFileData) and works out how to restore it exactly. Stops if it can't
   (built-in slot, base Photo Style without a known tag, or Photo Style isn't Real Time LUT).
3. With --yes: uploads a constant-red test LUT into that slot under the same name, checks live view and a second shot,
   then restores the original and proves it by reading the slot back (same data lines, same base Photo Style).
Results go to OUTDIR/result.json. Test shots are deleted from the card by gphoto2 after download.
"""
import io
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from PIL import Image, ImageStat  # noqa: E402

from lumix import batch, capture, liveview, luts  # noqa: E402
from lumix.ptp import Camera, CameraError  # noqa: E402

TAGGABLE = {'STD', 'NAT', 'VIVD', 'MONO', 'SCNY', 'PORT'}


def data_lines(text):
    return [tuple(float(v) for v in l.split()) for l in text.split('\n')
            if l.strip() and (l.strip()[0].isdigit() or l.strip()[0] in '-.')]


def same_values(a, b, tol=1e-5):
    """The camera stores LUTs with 7-decimal floats, so compare numbers, not strings."""
    a, b = data_lines(a), data_lines(b)
    return len(a) == len(b) and all(abs(x - y) <= tol for p, q in zip(a, b) for x, y in zip(p, q))


def red_lut(size=33):
    return ('TITLE "swap test red"\nLUT_3D_SIZE %d\n' % size + '1.000000 0.000000 0.000000\n' * size ** 3).encode()


def lv_mean(cam, path):
    liveview.start(cam)
    try:
        for _ in range(8):  # let a few frames pass so the newest look is in the frame
            jpg, _ = liveview.frame(cam)
    finally:
        liveview.stop(cam)
    open(path, 'wb').write(jpg)
    return [round(v) for v in ImageStat.Stat(Image.open(io.BytesIO(jpg)).convert('RGB')).mean]


def jpg_mean(info):
    jpg = next((f for f in info['files'] if f.upper().endswith('.JPG')), None)
    if not jpg:
        return None
    im = Image.open(jpg).convert('RGB')
    im.thumbnail((640, 640))
    return [round(v) for v in ImageStat.Stat(im).mean]


def restore_blob(entry, original, manifest):
    """Bytes that put the slot back exactly, or raise. Prefer the source file of a LUT we loaded ourselves."""
    if manifest:
        for item in batch.read_manifest(manifest):
            if item['name'] == entry['name']:
                return batch.prepare(item['path'], item['name'], 'VLOG'), 'manifest source file (V-Log base)'
    base = entry['base_style']
    if base is None or base == 'VLOG':
        lines = [l for l in original.split('\n') if not l.startswith('#LUMIXPHOTOSTYLE')]
        return '\n'.join(lines).encode('ascii'), 'camera copy, no base tag'
    if base in TAGGABLE:
        lines = original.split('\n')
        lines = [l for l in lines if not l.startswith('#LUMIXPHOTOSTYLE')]
        at = 1 if lines and lines[0].startswith('TITLE') else 0
        lines.insert(at, f'#LUMIXPHOTOSTYLE {base}')
        return '\n'.join(lines).encode('ascii'), f'camera copy + #LUMIXPHOTOSTYLE {base}'
    raise CameraError(f'Base Photo Style {base!r} has no known .cube tag; not touching "{entry["name"]}".')


def main(argv):
    if not argv:
        sys.exit(__doc__)
    out = os.path.expanduser(argv[0])
    os.makedirs(out, exist_ok=True)
    manifest = next((a.split('=', 1)[1] for a in argv if a.startswith('--manifest=')), None)
    go = '--yes' in argv
    slot = next((int(a.split('=', 1)[1]) for a in argv if a.startswith('--slot=')), None)
    r = {'steps': []}
    log = lambda m: (r['steps'].append(m), print(m))

    with Camera() as cam:
        lib = luts.library(cam)
        r['lv_before'] = lv_mean(cam, os.path.join(out, 'lv_before.jpg'))
    log(f"live view before: mean RGB {r['lv_before']}")
    if slot is None:
        shot = capture.capture(out, 'shot_before')
        a = shot.get('applied', {})
        r['shot_before'] = {'applied': a, 'mean': jpg_mean(shot)}
        log(f"shot before: LUT1Name={a.get('LUT1Name')!r} PhotoStyle={a.get('PhotoStyle')!r} mean {r['shot_before']['mean']}")
        name = str(a.get('LUT1Name', '')).strip()
    else:
        name = next((e['name'] for e in lib if e['position'] == slot), '')
        log(f'slot given: {slot} ({name!r}); live view only')
    entry = next((e for e in lib if e['name'] == name), None)
    if not entry:
        raise CameraError(f'Active LUT {name!r} not found in the library; is Photo Style set to Real Time LUT?')
    if entry['position'] in luts.FIXED_POSITIONS:
        raise CameraError(f'Active LUT is the built-in "{name}" (position {entry["position"]}); select a user LUT first.')
    r['slot'] = {k: entry[k] for k in ('position', 'name', 'base_style')}
    log(f"active slot: {entry['position']} {name!r} base {entry['base_style']}")

    with Camera() as cam:
        original = luts.download(cam, entry['position'])
    open(os.path.join(out, f"slot{entry['position']:02d}_original.cube"), 'w').write(original)
    blob, how = restore_blob(entry, original, manifest)
    r['restore_with'] = how
    log(f'restore plan: {how}')
    if not go:
        log('dry run: nothing written (add --yes)')
        json.dump(r, open(os.path.join(out, 'result.json'), 'w'), indent=2)
        return 0

    try:
        with Camera() as cam:
            e = luts.upload(cam, red_lut(), entry['position'], name)
            log(f"uploaded red test LUT into {entry['position']} as {name!r}: base {e and e['base_style']}")
            time.sleep(1.0)
            r['lv_swapped'] = lv_mean(cam, os.path.join(out, 'lv_swapped.jpg'))
        log(f"live view after swap: mean RGB {r['lv_swapped']}")
        if slot is None:
            shot = capture.capture(out, 'shot_swapped')
            a2 = shot.get('applied', {})
            r['shot_swapped'] = {'applied': a2, 'mean': jpg_mean(shot)}
            log(f"shot after swap: LUT1Name={a2.get('LUT1Name')!r} mean {r['shot_swapped']['mean']}")
    finally:
        with Camera() as cam:
            luts.upload(cam, blob, entry['position'], name)
            back = luts.download(cam, entry['position'])
            after = next(x for x in luts.library(cam) if x['position'] == entry['position'])
            r['lv_restored'] = lv_mean(cam, os.path.join(out, 'lv_restored.jpg'))
        same_data = same_values(back, original)
        same_base = after['base_style'] == entry['base_style'] or (entry['base_style'] in (None, 'VLOG')
                                                                  and after['base_style'] in (None, 'VLOG'))
        r['restored'] = {'name': after['name'], 'base_style': after['base_style'], 'same_data': same_data,
                         'same_base': same_base}
        log(f"restored: {after['name']!r} base {after['base_style']} data identical={same_data} base same={same_base}; "
            f"live view mean {r['lv_restored']}")
        json.dump(r, open(os.path.join(out, 'result.json'), 'w'), indent=2, default=str)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1:]))
    except CameraError as e:
        print(f'stopped: {e}', file=sys.stderr)
        sys.exit(2)
