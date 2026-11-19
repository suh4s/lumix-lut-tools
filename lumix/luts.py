"""LUT Library: list, upload (one transfer), rename, move, delete; plus .cube helpers (grain tags)."""
import struct

from .ptp import OK, CameraError

GET_PARAM, SET_PARAM = 0x9414, 0x9415
LUT_GET, LUT_START, LUT_CANCEL, LUT_SEND = 0x9921, 0x9922, 0x9923, 0x9924
GET_LUT_INFO, GET_LUT_FILE, RENAME, DELETE, SORT = 0x53000011, 0x53000012, 0x53000013, 0x53000014, 0x53000015
MAX_FILE = 9437184
FIXED_POSITIONS = (0, 1)  # Vlog_709 and the ARRI 709 sample on a DC-S9
STYLES = {0: 'STD', 1: 'VIVD', 2: 'NAT', 3: 'MONO', 4: 'SCNY', 5: 'PORT', 18: 'VLOG', 40: 'VLOG', 65535: None}
TYPES = {1: 'built-in', 2: 'sample', 3: 'user'}


def name256(name):
    b = name.encode('ascii', 'replace')[:255]
    return b + b'\0' * (256 - len(b))


def parse_lutinfo(d):
    """GetLutInfo reply -> entries (gaps skipped). 41 records of 262 B (PC Tether) or 272 B (LUMIX Lab mode)."""
    code, size = struct.unpack('<II', d[:8])
    if code != GET_LUT_INFO:
        raise ValueError(f'unexpected code 0x{code:08x}')
    rec = (size - 1) // 41 if (size - 1) % 41 == 0 else 272
    out = []
    for i in range((len(d) - 9) // rec):
        o = 9 + i * rec
        name = d[o + 2:o + 258].split(b'\0')[0].decode('ascii', 'replace')
        if not name:
            continue
        style, typ = struct.unpack('<HH', d[o + 258:o + 262])
        out.append({'position': i, 'name': name, 'base_style': STYLES.get(style, style),
                    'type': TYPES.get(typ, typ), 'on_vlog_default': typ == 3 and style == 65535})
    return out


def library(cam):
    rc, _, d = cam.tx(GET_PARAM, [GET_LUT_INFO])
    if rc != OK:
        raise CameraError(f'GetLutInfo failed 0x{rc:04x}')
    return parse_lutinfo(d)


def names_by_position(cam):
    entries = library(cam)
    n = max((e['position'] for e in entries), default=-1) + 1
    names = [''] * n
    for e in entries:
        names[e['position']] = e['name']
    return names


def find(cam, name):
    names = names_by_position(cam)
    if name not in names:
        raise CameraError(f'No LUT named "{name}" in the library.')
    return names.index(name)


def upload(cam, blob, slot, name):
    """Send a .cube into a slot (new or replace) in ONE transfer, as LUMIX Lab does over USB."""
    if not 0 <= slot <= 40 or slot in FIXED_POSITIONS:
        raise CameraError(f'Slot {slot} not allowed (0-40, not {FIXED_POSITIONS}).')
    if len(blob) > MAX_FILE:
        raise CameraError('File larger than 9 MB.')
    rc, _, _ = cam.tx(LUT_START, [slot], name256(name))
    if rc != OK:
        raise CameraError(f'LutStartFileData refused: 0x{rc:04x}')
    rc, _, _ = cam.tx(LUT_SEND, [len(blob)], blob, timeout=60000)
    if rc != OK:
        cam.tx(LUT_CANCEL, [])
        raise CameraError(f'LutSendFileData refused: 0x{rc:04x}')
    return next((e for e in library(cam) if e['position'] == slot), None)


def rename(cam, position, name):
    rc, _, _ = cam.tx(SET_PARAM, [RENAME], struct.pack('<IIH', RENAME, 258, position) + name256(name))
    if rc != OK:
        raise CameraError(f'Rename refused: 0x{rc:04x}')


def sort(cam, src, dst):
    rc, _, _ = cam.tx(SET_PARAM, [SORT], struct.pack('<IIHH', SORT, 4, src, dst))
    if rc != OK:
        raise CameraError(f'Move {src} -> {dst} refused: 0x{rc:04x} (positions {FIXED_POSITIONS} are fixed)')


def move(cam, name, position):
    src = find(cam, name)
    if src != position:
        sort(cam, src, position)
    return names_by_position(cam)


def close_gaps(cam):
    names = names_by_position(cam)
    while '' in names:
        g = names.index('')
        if g + 1 >= len(names):
            break
        sort(cam, g + 1, g)
        names = names_by_position(cam)
    return names


def delete(cam, name):
    pos = find(cam, name)
    if pos in FIXED_POSITIONS:
        raise CameraError('Built-in entries cannot be deleted.')
    rc, _, _ = cam.tx(SET_PARAM, [DELETE], struct.pack('<IIH', DELETE, 2, pos))
    if rc != OK:
        raise CameraError(f'Delete refused: 0x{rc:04x}')
    return close_gaps(cam)


def cube_with_tags(text, style='STD', grain=None, color_noise=None, title=None):
    """Rewrite a .cube with a camera header: TITLE, #LUMIXPHOTOSTYLE, optional #GRAINEFFECT/#COLORNOISE, LUT_3D_SIZE.
    Grain L1-33 Low, L34-66 Standard, L67-100 High. Verified on a DC-S9 2.1.
    style=None (or 'VLOG'/'none') leaves out #LUMIXPHOTOSTYLE: the camera then applies the LUT on V-Log (base 65535),
    which is right for V-Log LUTs such as most creator packs and Resolve exports."""
    if style is not None and style.upper() in ('VLOG', 'V-LOG', 'NONE'):
        style = None
    lines = text.split('\n')
    data = [l.strip() for l in lines if l.strip() and (l.strip()[0].isdigit() or l.strip()[0] in '-.')]
    size = round(len(data) ** (1 / 3))
    if size ** 3 != len(data) or size > 33:
        raise CameraError(f'Not a 3D LUT of at most 33 points ({len(data)} data lines).')
    title = title or next((l.split('"')[1] for l in lines if l.startswith('TITLE') and '"' in l), 'LUT')
    hdr = [f'TITLE "{title}"'] + ([f'#LUMIXPHOTOSTYLE {style}'] if style else [])
    if grain is not None:
        hdr.append(f'#GRAINEFFECT L{int(grain)}')
    if color_noise is not None:
        hdr.append(f'#COLORNOISE L{int(color_noise)}')
    hdr.append(f'LUT_3D_SIZE {size}')
    return '\n'.join(hdr + data) + '\n'


def download(cam, position):
    """Read a LUT back from the camera as .cube text (LutGetFileData: code + slot byte in the command block).
    Reply: [0x53000012 u32][size u32][file length u32][file]."""
    rc, d = cam.tx_raw(LUT_GET, struct.pack('<I', GET_LUT_FILE) + bytes([position]), timeout=60000)
    if rc != OK or len(d) < 12:
        raise CameraError(f'LutGetFileData refused for position {position}: 0x{rc:04x}')
    n = struct.unpack('<I', d[8:12])[0]
    return d[12:12 + n].decode('ascii', 'replace')


def backup(cam, directory):
    """Download every user LUT into directory as '<pos>_<name>.cube'. Returns the written paths."""
    import os
    os.makedirs(directory, exist_ok=True)
    out = []
    for e in library(cam):
        if e['type'] != 'user':
            continue
        safe = ''.join(c if c.isalnum() or c in ' ._-' else '_' for c in e['name']).strip() or 'lut'
        if not safe.lower().endswith('.cube'):
            safe += '.cube'
        path = os.path.join(directory, f"{e['position']:02d}_{safe}")
        open(path, 'w').write(download(cam, e['position']))
        out.append(path)
    return out
