#!/usr/bin/env python3
"""Read-only probe of My Photo Style (firmware 2.0+) and the Photo Style / LUT properties around it.

  mps_probe.py [OUT.json]

Only GetProperty (0x9402) and ListProperty (0x9108) are sent: nothing on the camera changes.
Layouts decoded from a DC-S9 on firmware 2.1 (docs/scans/props_fw2.1_tether.txt) and LUMIX Lab 3.1.0's builders:
  0x0200022A  My Photo Style names: records of [u16 style code][23 B ASCII name]
  0x02000220  My Photo Style display list (0x02000221 the same, writable twin): [u16 code][u16 shown]
  0x02000222  Photo Style display list (which styles the menu shows): [u16 code][u16 shown]
  0x02000223  My Photo Style select setting (style being edited / selected)
  0x020001F0-FC  LUT block: base style, LUT 1/2 on + registration, strength, colour noise, dual native ISO, gain
  0x02000012-1C  Photo Style fine-tuning of the current style (contrast, sharpness, NR, saturation, ...)
"""
import json
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from lumix import settings  # noqa: E402
from lumix.ptp import Camera  # noqa: E402

STYLE_NAMES = {0: 'Standard', 1: 'Vivid', 2: 'Natural', 3: 'Monochrome', 4: 'Scenery', 5: 'Portrait',
               15: 'L.Monochrome', 17: 'Cinelike D2', 18: 'V-Log', 49: 'Real Time LUT'}
FINE = {0x02000012: 'contrast', 0x02000013: 'sharpness', 0x02000014: 'noise_reduction', 0x02000015: 'saturation',
        0x02000016: 'filter', 0x02000017: 'hue', 0x02000018: 'grain', 0x02000019: 'p19', 0x0200001A: 'color',
        0x0200001B: 'highlight', 0x0200001C: 'shadow'}
LUTBLOCK = {0x020001F0: 'lut_block', 0x020001F1: 'dual_native_iso', 0x020001F2: 'color_noise', 0x020001F3: 'lut1_on',
            0x020001F6: 'gain', 0x020001F7: 'base_photo_style', 0x020001F8: 'lut2_on', 0x020001F9: 'lut1_strength',
            0x020001FA: 'lut2_strength', 0x020001FB: 'lut1_registration', 0x020001FC: 'lut2_registration'}


def pairs(hexstr):
    b = bytes.fromhex(hexstr or '')
    return [struct.unpack('<HH', b[i:i + 4]) for i in range(0, len(b) - 3, 4)]


def names(hexstr):
    b = bytes.fromhex(hexstr or '')
    out = []
    for i in range(0, len(b) - 24, 25):
        code = struct.unpack('<H', b[i:i + 2])[0]
        out.append((code, b[i + 2:i + 25].split(b'\0')[0].decode('ascii', 'replace')))
    return out


def signed(v):
    return v - 0x10000 if isinstance(v, int) and v >= 0x8000 else v


def main(argv):
    r = {}
    with Camera() as cam:
        r['photo_style'] = settings.get(cam, 0x02000010)
        r['photo_style_selectable'] = settings.allowed(cam, 0x02000011, 2)
        r['mps_select'] = settings.get(cam, 0x02000223)
        r['mps_select_allowed'] = settings.allowed(cam, 0x02000223, 2)
        r['mps_registration'] = settings.get(cam, 0x02000224)
        r['mps_names'] = names(settings.get(cam, 0x0200022A))
        r['mps_display'] = pairs(settings.get(cam, 0x02000220))
        r['style_display'] = pairs(settings.get(cam, 0x02000222))
        r['fine_tune'] = {n: signed(settings.get(cam, p)) for p, n in FINE.items()}
        r['lut_block'] = {n: settings.get(cam, p) for p, n in LUTBLOCK.items()}
        r['p0210_021c'] = {f'0x{p:08x}': settings.get(cam, p) for p in range(0x02000210, 0x0200021D)}
        r['p0225_0229'] = {f'0x{p:08x}': settings.get(cam, p) for p in range(0x02000225, 0x0200022A)}
    style = lambda c: STYLE_NAMES.get(c) or dict(r['mps_names']).get(c) or f'code {c}'
    print(f"Photo Style now: {style(r['photo_style'])} ({r['photo_style']})")
    print('Selectable over USB (0x02000011):', ', '.join(f'{c} {style(c)}' for c in r['photo_style_selectable']))
    print('My Photo Style slots:')
    shown = dict(r['mps_display'])
    for code, name in r['mps_names']:
        print(f'  {code:3d}  {name or "(no name)":24s} shown in menu: {bool(shown.get(code, 0)) if code in shown else "-"}')
    print('MPS select setting:', r['mps_select'], ' allowed:', r['mps_select_allowed'], ' registration:', r['mps_registration'])
    print('Styles shown in the Photo Style menu:', ', '.join(f'{c}{"" if s else " (hidden)"}' for c, s in r['style_display']))
    print('Fine-tuning of the current style:', r['fine_tune'])
    print('LUT block:', r['lut_block'])
    if len(argv) > 0:
        json.dump(r, open(argv[0], 'w'), indent=2)
        print('saved', argv[0])


if __name__ == '__main__':
    main(sys.argv[1:])
