"""Panasonic vendor properties: read, list allowed values, write, with name <-> value mappings verified on a DC-S9."""
import struct
import time

from .ptp import OK, CameraError

GET, SET, LIST = 0x9402, 0x9403, 0x9108

# (read prop, write prop, size). Some properties only accept writes through their "param" twin.
PROPS = {
    'photo_style': (0x02000010, 0x02000011, 2),
    'image_format': (0x020000A2, 0x020000A2, 2),
    'white_balance': (0x02000050, 0x02000051, 2),
    'kelvin': (0x02000052, 0x02000052, 2),
    'wb_adjust_gm': (0x02000054, 0x02000054, 2),
    'iso': (0x02000020, 0x02000021, 4),
    'exposure_comp': (0x02000060, 0x02000061, 2),
    'aperture': (0x02000040, 0x02000041, 2),
    'shutter': (0x02000030, 0x02000031, 4),
    'color_noise': (0x020001F2, 0x020001F2, 2),
    'lut1_on': (0x020001F3, 0x020001F3, 2),
    'lut1_opacity': (0x020001F9, 0x020001F9, 2),
    'focus_mode': (0x02000071, 0x02000071, 2),  # AFArea_AFModeParam; 0x02000070 reads the same
}

PHOTO_STYLES = {'standard': 0, 'vivid': 1, 'natural': 2, 'monochrome': 3, 'scenery': 4, 'portrait': 5,
                'l_monochrome': 15, 'cinelike_d2': 17, 'vlog': 18, 'real_time_lut': 49,
                # My Photo Style 1-10 (firmware 2.0+, DC-S9 2.1): codes 19-28, names in 0x0200022A. Only slots shown in
                # the Photo Style menu (0x02000220/0x02000221) are offered by 0x02000011 (control/mps_probe.py).
                **{f'my_photo_style_{i}': 18 + i for i in range(1, 11)}}
WB_MODES = {'auto': 0x0002, 'daylight': 0x0004, 'fluorescent': 0x0005, 'tungsten': 0x0006, 'flash': 0x0007,
            'cloudy': 0x8008, 'white_set': 0x8009, 'shade': 0x800F, 'kelvin': 0x8010, 'kelvin2': 0x8011,
            'kelvin3': 0x8012, 'kelvin4': 0x8013, 'auto_cool': 0x8014, 'auto_warm': 0x8015}
FOCUS_MODES = {'AFS': 4, 'AFC': 5, 'MF': 3}  # DC-S9: 5 = AFC (camera screen), 3 = MF (MF drive works); 4 assumed AFS
# 1/3-stop f-number scale (x10) as cameras display it
APERTURE_THIRDS = {10, 11, 12, 14, 16, 18, 20, 22, 25, 28, 32, 35, 40, 45, 50, 56, 63, 71, 80, 90, 100, 110, 130, 140,
                   160, 180, 200, 220, 250, 290, 320, 360, 400, 450}
IMAGE_FORMATS = {'raw': 3, 'raw+jpeg': 4}
ISO_AUTO = 0xFFFFFFFF


def _name(table, value):
    return next((k for k, v in table.items() if v == value), value)


def ev_encode(ev):
    """EV in stops -> Panasonic 1/3-stop code (negative values have bit 15 set)."""
    thirds = round(ev * 3)
    return (0x8000 | -thirds) if thirds < 0 else thirds


def ev_decode(code):
    return round(-(code & 0x7FFF) / 3 if code & 0x8000 else code / 3, 2)


def get(cam, prop):
    rc, _, d = cam.tx(GET, [prop])
    if rc != OK or len(d) < 8:
        return None
    size = struct.unpack('<I', d[4:8])[0]
    if size in (2, 4):
        return struct.unpack('<H' if size == 2 else '<I', d[8:8 + size])[0]
    return d[8:8 + size].hex()


def allowed(cam, prop, size):
    rc, _, d = cam.tx(LIST, [prop, 0, 0])
    if rc != OK or len(d) < 32:
        return []
    o = struct.unpack('<I', d[4:8])[0] * 4 + 8
    fmt = '<I' if size == 4 else '<H'
    n = struct.unpack('<I', d[o + size:o + size + 4])[0]
    b = o + size + 4
    return [struct.unpack(fmt, d[b + i * size:b + (i + 1) * size])[0] for i in range(n) if b + (i + 1) * size <= len(d)]


def put(cam, prop, value, size):
    payload = struct.pack('<II', prop, size) + (struct.pack('<H', value) if size == 2 else struct.pack('<I', value))
    for _ in range(20):  # camera answers 0x2019 DeviceBusy while live view is streaming: retry
        rc, _, _ = cam.tx(SET, [prop], payload)
        if rc != 0x2019:
            return rc
        time.sleep(0.03)
    return rc


def read(cam, key):
    rp, _, _ = PROPS[key]
    return get(cam, rp)


def write(cam, key, value):
    """Write a setting, verify by reading back. Returns (old, new)."""
    rp, wp, size = PROPS[key]
    old = get(cam, rp)
    rc = put(cam, wp, value, size)
    if rc != OK and wp != rp:
        rc = put(cam, rp, value, size)
    new = get(cam, rp)
    if rc != OK or new != value:
        raise CameraError(f'{key}: camera did not accept {value} (rc 0x{rc:04x}, reads {new})')
    return old, new


def status(cam):
    r = {k: read(cam, k) for k in PROPS}
    return {
        'photo_style': _name(PHOTO_STYLES, r['photo_style']),
        'image_format': _name(IMAGE_FORMATS, r['image_format']),
        'white_balance': _name(WB_MODES, r['white_balance']),
        'kelvin': r['kelvin'],
        'wb_adjust_gm': r['wb_adjust_gm'],
        'iso': 'auto' if r['iso'] == ISO_AUTO else r['iso'],
        'exposure_comp_ev': ev_decode(r['exposure_comp']) if r['exposure_comp'] is not None else None,
        'aperture_f': r['aperture'] / 10 if r['aperture'] else None,
        'color_noise': bool(r['color_noise']) if r['color_noise'] is not None else None,
        'lut1_on': bool(r['lut1_on']) if r['lut1_on'] is not None else None,
        'lut1_opacity': r['lut1_opacity'],
    }


def shutter_label(v):
    if v in (None, 0xFFFFFFFF):
        return 'auto/bulb'
    if v & 0x80000000:
        s = (v & 0x7FFFFFFF) / 1000
        return f'{s:g}"'
    return f'1/{v / 1000:g}'


def options(cam):
    """Values the camera currently offers per setting: {key: [(label, raw value)]}."""
    def lst(key):
        rp, _, size = PROPS[key]
        return allowed(cam, rp, size)
    out = {}
    out['photo_style'] = [(n, v) for n, v in PHOTO_STYLES.items() if v in set(lst('photo_style'))]
    out['white_balance'] = [(n, v) for n, v in WB_MODES.items() if v in set(lst('white_balance'))]
    out['iso'] = [('auto' if v == ISO_AUTO else str(v), v) for v in lst('iso') if v == ISO_AUTO or v < 0x10000000]
    out['exposure_comp'] = sorted(((f'{ev_decode(v):+g} EV', v) for v in lst('exposure_comp')), key=lambda x: ev_decode(x[1]))
    av = lst('aperture')  # the camera lists every 0.1 (f/2, 2.1, 2.2 ...): keep the real 1/3-stop scale
    cur_av = get(cam, PROPS['aperture'][0])
    out['aperture'] = [(f'f/{v / 10:g}', v) for v in sorted(av)
                       if v in APERTURE_THIRDS or v == min(av) or v == cur_av]
    out['shutter'] = [(shutter_label(v), v) for v in lst('shutter')]
    out['image_format'] = [(n, v) for n, v in IMAGE_FORMATS.items()]
    fm = set(lst('focus_mode'))
    out['focus_mode'] = [(n, v) for n, v in FOCUS_MODES.items() if v in fm] + \
                        [(f'focus {v}', v) for v in sorted(fm) if v not in FOCUS_MODES.values()]
    out['lut1_opacity'] = [(f'{v}%', v) for v in (lst('lut1_opacity') or list(range(10, 101, 10)))]
    out['color_noise'] = [('off', 0), ('on', 1)]
    out['kelvin'] = [(f'{k} K', k) for k in range(2500, 10001, 100)]
    return {k: v for k, v in out.items() if v}


CAMERA_MODES = {1: 'A'}  # 0x02000080; only A verified on a DC-S9 (allowed 0 1 9 4 3)
CARD_INFO, SHOTS_LEFT = 0x12000010, 0x12000011


def hud(cam):
    """Camera-screen values that aren't settable: battery %, remaining shots, mode, shutter (in A/P)."""
    out = {}
    try:
        rc, _, d = cam.tx(0x1015, [0x5001])  # standard GetDevicePropValue BatteryLevel
        if rc == OK and d:
            out['battery'] = d[0]
    except CameraError:
        raise
    except Exception:
        pass
    rc, _, d = cam.tx(0x9414, [CARD_INFO])  # GetParamInfo: [u32 code][u32 size][value] entries
    o = 0
    while rc == OK and o + 8 <= len(d):
        code, size = struct.unpack('<II', d[o:o + 8])
        if code == SHOTS_LEFT and size == 4:
            out['shots'] = struct.unpack('<I', d[o + 8:o + 12])[0]
        o += 8 + size
    m = get(cam, 0x02000080)
    if m is not None:
        out['mode'] = CAMERA_MODES.get(m, f'mode {m}')
    sh = read(cam, 'shutter')
    if sh is not None:
        out['shutter'] = shutter_label(sh)
    return out


def label_of(opts, key, raw):
    return next((l for l, v in opts.get(key, []) if v == raw), str(raw))
