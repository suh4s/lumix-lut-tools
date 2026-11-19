#!/usr/bin/env python3
"""Can code write a My Photo Style slot and select it? (firmware 2.0+, tested on a DC-S9 2.1)

  mps_write_test.py OUTDIR [--slot=10] [--lut-a=40] [--lut-b=29] [--yes]

Without --yes: reads the current state and prints the plan; nothing is written.
With --yes, on one unused slot (default 10, code 28):
  1. records Photo Style, My Photo Style select/registration/display list, names, fine-tune and the LUT block
  2. live view on the current style, then on plain V-Log (Photo Style 18)
  3. show the slot in the menu (0x02000221); edit session (0x9419 StartEdit): select the slot (0x02000223) and confirm
     it, read its LUT settings, register LUT A in LUT 1 at strength 100, EndEdit; select it as Photo Style
     (0x02000011); live view
  4. the same with LUT B, then with LUT 1 off; live view each time
  5. always (finally): Photo Style, slot contents, menu display and select setting back to what step 1 recorded,
     then a read-back compared with step 1
Pass = the frames for LUT A and LUT B differ clearly, and both differ from LUT off.
Writes only settings the camera's own My Photo Style menu offers (docs/SAFETY.md rule 3). LUT files are not touched.
No slot is written unless a read-back confirms the edit session is on it. Refused writes (0x201D) are retried: the S9
refuses Photo Style writes for a moment after a style change.
"""
import io
import json
import os
import struct
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from lumix import liveview, settings  # noqa: E402
from lumix.ptp import OK, Camera, CameraError  # noqa: E402

CTRL = 0x9419
START_EDIT, END_EDIT, RESET = 0x03010041, 0x03010042, 0x03010044
STYLE_R, STYLE_W = 0x02000010, 0x02000011
DISPLAY_R, DISPLAY_W = 0x02000220, 0x02000221
SELECT, REGISTRATION, NAMES = 0x02000223, 0x02000224, 0x0200022A
VLOG, RTL = 18, 49
SLOT_PROPS = {'base_photo_style': 0x020001F7, 'lut1_on': 0x020001F3, 'lut1_registration': 0x020001FB,
              'lut1_strength': 0x020001F9, 'lut2_on': 0x020001F8, 'lut2_registration': 0x020001FC,
              'lut2_strength': 0x020001FA, 'color_noise': 0x020001F2}
SENTINELS = {65533, 65534, 65535}
RETRY_RC = {0x2019, 0x201D}  # busy / refused while a style change settles: retry for up to ~3 s


def ctrl(cam, code, u16=None):
    data = struct.pack('<II', code, 0) if u16 is None else struct.pack('<IIH', code, 2, u16)
    rc, _, _ = cam.tx(CTRL, [code], data)
    return rc


def pairs(hexstr):
    b = bytes.fromhex(hexstr or '')
    return [list(struct.unpack('<HH', b[i:i + 4])) for i in range(0, len(b) - 3, 4)]


def snapshot(cam):
    return {'photo_style': settings.get(cam, STYLE_R), 'select': settings.get(cam, SELECT),
            'registration': settings.get(cam, REGISTRATION), 'display': pairs(settings.get(cam, DISPLAY_R)),
            'names': settings.get(cam, NAMES),
            'slot_props': {k: settings.get(cam, p) for k, p in SLOT_PROPS.items()}}


def stats(jpg):
    from PIL import Image, ImageStat
    im = Image.open(io.BytesIO(jpg)).convert('RGB')
    r, g, b = ImageStat.Stat(im).mean
    s = ImageStat.Stat(im.convert('HSV')).mean[1]
    y = ImageStat.Stat(im.convert('L'))
    return {'rgb': [round(r, 1), round(g, 1), round(b, 1)], 'luma': round(y.mean[0], 1),
            'contrast': round(y.stddev[0], 1), 'saturation': round(s, 1), 'warmth': round(r - b, 1)}


def grab(cam, outdir, name, settle=10):
    liveview.start(cam)
    try:
        time.sleep(0.4)
        for _ in range(settle):  # let the new look reach the stream
            jpg, _ = liveview.frame(cam)
    finally:
        liveview.stop(cam)
    open(os.path.join(outdir, name + '.jpg'), 'wb').write(jpg)
    st = stats(jpg)
    print(f'  frame {name:10s} {st}')
    return st


def put(cam, prop, value, size=2, log=None, tries=12, wait=0.25):
    for i in range(tries):
        rc = settings.put(cam, prop, value, size)
        if rc not in RETRY_RC:
            break
        time.sleep(wait)
    back = settings.get(cam, prop)
    line = f'  set 0x{prop:08x} = {value}: rc 0x{rc:04x}, reads {back}' + (f' (after {i + 1} tries)' if i else '')
    print(line)
    if log is not None:
        log.append(line.strip())
    return rc, back


def select_for_edit(cam, code, log):
    """Inside an edit session: select `code` and confirm it, or raise before anything is written."""
    put(cam, SELECT, code, log=log)
    now = settings.get(cam, SELECT)
    if now != code:
        raise CameraError(f'Edit session is on slot code {now}, not {code}: nothing written to any slot')


def edit_slot(cam, code, values, log):
    """One edit session: select the slot, write `values` (prop name -> value), end. Returns the values read
    right after selecting the slot (its contents before this session's writes)."""
    rc = ctrl(cam, START_EDIT)
    log.append(f'StartEdit rc 0x{rc:04x}')
    print(f'  StartEdit: rc 0x{rc:04x}')
    try:
        select_for_edit(cam, code, log)
        before = {k: settings.get(cam, p) for k, p in SLOT_PROPS.items()}
        print(f'  slot {code} reads {before}')
        for k, v in values.items():
            put(cam, SLOT_PROPS[k], v, log=log)
    finally:
        rc = ctrl(cam, END_EDIT)
        log.append(f'EndEdit rc 0x{rc:04x}')
        print(f'  EndEdit: rc 0x{rc:04x}')
    return before


def show(cam, code, shown, log):
    return put(cam, DISPLAY_W, code | (shown << 16), size=4, log=log)


def differ(a, b, tol=6.0):
    keys = ('luma', 'contrast', 'saturation', 'warmth')
    return max(abs(a[k] - b[k]) for k in keys) > tol


def run(cam, outdir, slot, lut_a, lut_b, log):
    code = 18 + slot
    result = {'slot': slot, 'code': code, 'lut_a': lut_a, 'lut_b': lut_b, 'frames': {}}
    rec = snapshot(cam)
    result['recorded'] = rec
    print('Recorded:', json.dumps({k: v for k, v in rec.items() if k != 'names'}))
    allowed_styles = settings.allowed(cam, STYLE_W, 2)
    if VLOG not in allowed_styles:
        raise CameraError('V-Log is not selectable as a Photo Style right now')
    f = result['frames']
    f['start'] = grab(cam, outdir, 'start')
    if f['start']['luma'] < 8 or f['start']['contrast'] < 2:
        raise CameraError('Live view is nearly black or flat (lens cap on?). Point the camera at a lit, '
                          'colourful scene and rerun.')
    original = None
    try:
        put(cam, STYLE_W, VLOG, log=log)
        f['vlog'] = grab(cam, outdir, 'vlog')
        show(cam, code, 1, log)  # a hidden slot could not be selected for editing on the S9 (fw 2.1)
        result['selectable_after_show'] = code in settings.allowed(cam, STYLE_W, 2)
        print(f'Slot {slot}: register LUT A ({lut_a})')
        original = edit_slot(cam, code, {'lut1_registration': lut_a, 'lut1_on': 1, 'lut1_strength': 100}, log)
        result['slot_before'] = original
        put(cam, STYLE_W, code, log=log)
        result['slot_selected'] = {k: settings.get(cam, p) for k, p in SLOT_PROPS.items()}
        print(f'  selected; slot reads {result["slot_selected"]}')
        f['lut_a'] = grab(cam, outdir, 'lut_a')
        print(f'Slot {slot}: register LUT B ({lut_b})')
        put(cam, STYLE_W, VLOG, log=log)
        edit_slot(cam, code, {'lut1_registration': lut_b}, log)
        put(cam, STYLE_W, code, log=log)
        f['lut_b'] = grab(cam, outdir, 'lut_b')
        print(f'Slot {slot}: LUT 1 off')
        put(cam, STYLE_W, VLOG, log=log)
        edit_slot(cam, code, {'lut1_on': 0}, log)
        put(cam, STYLE_W, code, log=log)
        f['lut_off'] = grab(cam, outdir, 'lut_off')
    finally:
        print('Restoring')
        restore(cam, rec, code, original, log)
        result['after'] = snapshot(cam)
        f['end'] = grab(cam, outdir, 'end')
    a, b, off = f.get('lut_a'), f.get('lut_b'), f.get('lut_off')
    result['lut_applies'] = bool(a and b and off and differ(a, b) and differ(a, off) and differ(b, off))
    result['restored'] = all(result['after'][k] == rec[k] for k in ('photo_style', 'select', 'registration',
                                                                       'display', 'names'))
    return result


def restore(cam, rec, code, original, log):
    put(cam, STYLE_W, rec['photo_style'] if rec['photo_style'] is not None else RTL, log=log)
    readable = original and not any(v in SENTINELS or v is None for v in original.values())
    if readable:  # write the slot's own values back
        rc = ctrl(cam, START_EDIT)
        try:
            select_for_edit(cam, code, log)
            for k, v in original.items():
                if settings.get(cam, SLOT_PROPS[k]) != v:
                    put(cam, SLOT_PROPS[k], v, log=log)
        finally:
            ctrl(cam, END_EDIT)
    else:  # contents were not readable: reset the (unused) slot to its defaults
        rc = ctrl(cam, RESET, code)
        log.append(f'Reset slot {code} rc 0x{rc:04x}')
        print(f'  Reset slot {code}: rc 0x{rc:04x}')
    was_shown = dict((c, s) for c, s in rec['display']).get(code, 0)
    show(cam, code, was_shown, log)
    if rec['select'] is not None and settings.get(cam, SELECT) != rec['select']:
        put(cam, SELECT, rec['select'], log=log)


def main(argv):
    args = [a for a in argv if not a.startswith('--')]
    opts = dict(a[2:].split('=', 1) if '=' in a else (a[2:], '1') for a in argv if a.startswith('--'))
    if not args:
        sys.exit(__doc__)
    outdir = args[0]
    slot, lut_a, lut_b = int(opts.get('slot', 10)), int(opts.get('lut-a', 40)), int(opts.get('lut-b', 29))
    if not 1 <= slot <= 10 or lut_a == lut_b:
        sys.exit('slot must be 1-10 and the two LUTs must differ')
    os.makedirs(outdir, exist_ok=True)
    with Camera() as cam:
        if 'yes' not in opts:
            rec = snapshot(cam)
            print('Current state:', json.dumps({k: v for k, v in rec.items() if k != 'names'}))
            print(f'Plan: slot {slot} (code {18 + slot}), LUT A position {lut_a}, LUT B position {lut_b}. '
                  'Rerun with --yes to run it.')
            return 0
        log = []
        result = run(cam, outdir, slot, lut_a, lut_b, log)
    result['log'] = log
    json.dump(result, open(os.path.join(outdir, 'result.json'), 'w'), indent=1)
    print(f"\nLUT registration applies to the slot: {'YES' if result['lut_applies'] else 'NO'}")
    print(f"Restored to recorded state: {'yes' if result['restored'] else 'NO, see result.json'}")
    return 0 if result['restored'] else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
