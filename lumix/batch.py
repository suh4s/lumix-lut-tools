"""Load many .cube files into the LUT Library in one go: plan (read only), upload into free positions, verify each.

Manifest: one LUT per line, `path,name[,position]` (CSV; `#` starts a comment; relative paths are resolved against the
manifest's folder). Without a position the LUT goes into the next free position (2-40, gaps first).

Base Photo Style: `base='VLOG'` (or 'none') sends the file WITHOUT a `#LUMIXPHOTOSTYLE` tag, so the camera applies it on
V-Log (base 65535), which is what V-Log LUTs (most creator packs, Resolve exports) need. Any other value ('STD', 'NAT',
...) writes that tag, for LUTs made on a Standard/Natural image. `base=None` keeps the file's own tag, or none.
"""
import csv
import io
import os

from . import luts
from .ptp import CameraError

VLOG_BASES = {'VLOG', 'V-LOG', 'NONE'}
MAX_NAME = 95  # LUMIX Lab's limit; the camera takes 255 but its screens show far less


def read_manifest(path):
    """-> [{'path', 'name', 'position'}] in file order."""
    root = os.path.dirname(os.path.abspath(path))
    items = []
    with open(path, newline='') as f:
        rows = csv.reader(line for line in f if line.strip() and not line.lstrip().startswith('#'))
        for n, row in enumerate(rows, 1):
            row = [c.strip() for c in row]
            if len(row) < 2 or not row[0] or not row[1]:
                raise CameraError(f'Manifest line {n}: expected "path,name[,position]", got {row}.')
            pos = int(row[2]) if len(row) > 2 and row[2] else None
            p = os.path.expanduser(row[0])
            items.append({'path': p if os.path.isabs(p) else os.path.join(root, p), 'name': row[1], 'position': pos})
    if not items:
        raise CameraError(f'No LUTs listed in {path}.')
    return items


def check_name(name):
    if not name.isascii() or not name.isprintable():
        raise CameraError(f'Name "{name}" must be plain ASCII.')
    if len(name) > MAX_NAME:
        raise CameraError(f'Name "{name}" is longer than {MAX_NAME} characters.')
    return name


def prepare(path, name, base='VLOG'):
    """Read and validate a .cube; return the bytes to send. See the module docstring for `base`."""
    try:
        text = open(path, encoding='ascii', errors='strict').read()
    except UnicodeDecodeError:
        raise CameraError(f'{path}: not a plain-text .cube file.')
    check_name(name)
    has_tag = any(l.startswith('#LUMIXPHOTOSTYLE') for l in text.split('\n')[:20])
    if base is None:
        luts.cube_with_tags(text, style=None)  # validate the grid (raises if not a <=33-point 3D LUT)
        blob = text.encode('ascii') if has_tag else luts.cube_with_tags(text, style=None, title=name).encode('ascii')
    elif base.upper() in VLOG_BASES:
        blob = luts.cube_with_tags(text, style=None, title=name).encode('ascii')
    else:
        blob = luts.cube_with_tags(text, style=base.upper(), title=name).encode('ascii')
    if len(blob) > luts.MAX_FILE:
        raise CameraError(f'{path}: larger than 9 MB after rewriting.')
    return blob


def expected_base(base):
    """The base_style the library should report after upload (None = V-Log default), or '?' when unknown."""
    if base is None:
        return '?'
    return None if base.upper() in VLOG_BASES else base.upper()


def plan(library, items, replace=False):
    """Assign positions. library = luts.library(cam) output. -> [{..item, 'position', 'replaces'}]. Raises on conflicts."""
    taken = {e['position']: e['name'] for e in library}
    names_now = {e['name'] for e in library}
    wanted = [i['position'] for i in items if i['position'] is not None]
    if len(wanted) != len(set(wanted)):
        raise CameraError('The manifest gives the same position twice.')
    seen = set()
    for i in items:
        if i['name'] in seen:
            raise CameraError(f'The manifest uses the name "{i["name"]}" twice.')
        seen.add(i['name'])
    free = [p for p in range(2, 41) if p not in taken and p not in wanted]
    out = []
    for i in items:
        pos = i['position']
        if pos is None:
            if not free:
                n_free = sum(1 for p in range(2, 41) if p not in taken)
                raise CameraError(f'LUT Library is full: no free position for "{i["name"]}" '
                                  f'({len(items)} LUTs listed, {n_free} positions free).')
            pos = free.pop(0)
        if not 2 <= pos <= 40:
            raise CameraError(f'Position {pos} for "{i["name"]}" is not allowed (2-40).')
        if pos in taken and not replace:
            raise CameraError(f'Position {pos} holds "{taken[pos]}". Use replace to overwrite it.')
        if i['name'] in names_now and taken.get(pos) != i['name']:
            raise CameraError(f'A LUT named "{i["name"]}" is already in the library; rename it in the manifest.')
        out.append({**i, 'position': pos, 'replaces': taken.get(pos)})
    return out


def load(cam, items, base='VLOG', replace=False, log=print):
    """Upload planned items one by one and verify each against the library. Stops at the first failure.
    -> list of result dicts (one per item attempted)."""
    steps = plan(luts.library(cam), items, replace)
    blobs = [prepare(s['path'], s['name'], base) for s in steps]  # validate every file before the first write
    want = expected_base(base)
    results = []
    for s, blob in zip(steps, blobs):
        entry = luts.upload(cam, blob, s['position'], s['name'])
        got = entry['base_style'] if entry else None
        # a tag-less file is documented to report 65535 (None); LUTs added in camera can report V-Log (40) instead
        base_ok = want == '?' or got == want or (want is None and got == 'VLOG')
        ok = bool(entry) and entry['name'] == s['name'] and base_ok
        results.append({'position': s['position'], 'name': s['name'], 'bytes': len(blob), 'ok': ok,
                        'base_style': entry['base_style'] if entry else None, 'replaced': s['replaces']})
        log(f"{'ok ' if ok else 'BAD'} {s['position']:2d}  {s['name']}  ({len(blob)} B, base "
            f"{(entry or {}).get('base_style') or 'V-Log default'})")
        if not ok:
            raise CameraError(f'Verification failed for "{s["name"]}" at position {s["position"]}: {entry}')
    return results


def format_plan(steps, library):
    buf = io.StringIO()
    taken = {e['position']: e for e in library}
    buf.write(f'Library now: {len(library)} entries, {sum(1 for p in range(2, 41) if p not in taken)} free positions\n')
    for s in steps:
        note = f'  REPLACES "{s["replaces"]}"' if s['replaces'] else ''
        buf.write(f"  {s['position']:2d} <- {s['name']}  [{os.path.basename(s['path'])}]{note}\n")
    return buf.getvalue()
