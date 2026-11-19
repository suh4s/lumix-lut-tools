#!/usr/bin/env python3
"""LUT Library control over USB (camera USB mode PC (Tether) 04da:2382 or LUMIX Lab 04da:2387), using the commands LUMIX Lab 3.1.0 uses
(see docs/PROTOCOL.md "LUT Library protocol").

  lumix_lut.py selftest                     read-only checks: device, library, base styles
  lumix_lut.py list                         read the library (read only)
  lumix_lut.py push FILE.cube SLOT NAME     upload into an EMPTY slot (refuses occupied slots unless --replace)
  lumix_lut.py rename SLOT NAME
  lumix_lut.py sort FROM TO
  lumix_lut.py select SLOT                  make SLOT the active LUT 1

Safety: only the LUT ops 0x9922/0x9924/0x9923 and SetParamInfo LUT codes are sent; firmware ops (0x9606/0x9607) are
never used. Names are ASCII, max 255 bytes.
"""
import os, struct, subprocess, sys, time
import usb.core, usb.util, usb.backend.libusb1

VID = 0x04DA
GET_PARAM, SET_PARAM, SET_REC = 0x9414, 0x9415, 0x9403
LUT_START, LUT_CANCEL, LUT_SEND = 0x9922, 0x9923, 0x9924
GET_LUT_INFO, RENAME, DELETE, SORT = 0x53000011, 0x53000013, 0x53000014, 0x53000015
LUT1_REG = 0x020001FB
CHUNK = 61440
FORBIDDEN = {0x9606, 0x9607}  # firmware channel


class PTP:
    def __init__(self):
        subprocess.run(['killall', '-9', 'ptpcamerad'], capture_output=True)
        be = usb.backend.libusb1.get_backend(find_library=lambda x: '/opt/homebrew/lib/libusb-1.0.dylib')
        self.dev = usb.core.find(idVendor=VID, backend=be)
        if self.dev is None:
            sys.exit('LUMIX not found on USB (on, awake, USB mode LUMIX Lab?)')
        if self.dev.idProduct not in (0x2382, 0x2387):
            print(f'note: USB product 0x{self.dev.idProduct:04x} (expected 0x2382 PC Tether or 0x2387 LUMIX Lab)')
        try:
            self.dev.set_configuration()
        except usb.core.USBError:
            pass
        intf = self.dev.get_active_configuration()[(0, 0)]
        for i in range(8):
            try:
                usb.util.claim_interface(self.dev, 0); break
            except usb.core.USBError:
                if i == 7: raise
                subprocess.run(['killall', '-9', 'ptpcamerad'], capture_output=True); time.sleep(0.4)
        ep = lambda d: usb.util.find_descriptor(intf, custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == d and usb.util.endpoint_type(e.bmAttributes) == usb.util.ENDPOINT_TYPE_BULK)
        self.out, self.inp = ep(usb.util.ENDPOINT_OUT), ep(usb.util.ENDPOINT_IN)
        self.tid = 0
        rc, _, _ = self.tx(0x1002, [1])
        if rc not in (0x2001, 0x201E):
            sys.exit(f'OpenSession failed 0x{rc:04x}')

    def tx(self, code, params, data=None):
        assert code not in FORBIDDEN
        self.tid += 1
        self.out.write(struct.pack('<IHHI', 12 + 4 * len(params), 1, code, self.tid) + b''.join(struct.pack('<I', p) for p in params), timeout=5000)
        if data is not None:
            self.out.write(struct.pack('<IHHI', 12 + len(data), 2, code, self.tid) + data, timeout=30000)
        got = b''
        while True:
            buf = bytes(self.inp.read(1 << 20, timeout=30000))
            ln = struct.unpack('<I', buf[:4])[0]
            while len(buf) < ln:
                buf += bytes(self.inp.read(1 << 20, timeout=30000))
            typ, rc = struct.unpack('<HH', buf[4:8])
            if typ == 2:
                got += buf[12:ln]
            elif typ == 3:
                return rc, list(struct.unpack(f'<{(ln - 12) // 4}I', buf[12:12 + (ln - 12) // 4 * 4])), got

    def close(self):
        try:
            self.tx(0x1003, [])
        except Exception:
            pass
        usb.util.dispose_resources(self.dev)


def name256(s):
    b = s.encode('ascii', 'replace')[:255]
    return b + b'\0' * (256 - len(b))


STYLES = {0: 'STD', 1: 'VIVD', 2: 'NAT', 3: 'MONO', 4: 'SCNY', 5: 'PORT', 18: 'VLOG', 40: 'VLOG', 65535: 'none'}
TYPES = {1: 'built-in', 2: 'sample', 3: 'user'}


def parse_lutinfo(d):
    """GetLutInfo reply -> list of dicts (empty slots skipped). Layout: [code u32][size u32][u8 count] + 41 records.
    Record = [u16 id][256 B name][u16 photo style][u16 type] (262 B, PC Tether mode), followed in LUMIX Lab mode by
    [u16 grain][u16 colour noise][u16 sharpness][u16 NR][u16 visibility] (272 B)."""
    code, size = struct.unpack('<II', d[:8])
    if code != GET_LUT_INFO:
        raise ValueError(f'unexpected code 0x{code:08x}')
    rec = (size - 1) // 41 if (size - 1) % 41 == 0 else 272
    out = []
    for i in range((len(d) - 9) // rec):
        o = 9 + i * rec
        rid = struct.unpack('<H', d[o:o + 2])[0]
        name = d[o + 2:o + 258].split(b'\0')[0].decode('ascii', 'replace')
        style, typ = struct.unpack('<HH', d[o + 258:o + 262])
        extra = struct.unpack('<5H', d[o + 262:o + 272]) if rec >= 272 else (None,) * 5
        if name:
            out.append(dict(pos=i, id=rid, name=name, style=style, type=typ, grain=extra[0], color_noise=extra[1],
                            sharpness=extra[2], noise_reduction=extra[3], visibility=extra[4], record_size=rec))
    return out


def lut_info(p):
    rc, _, d = p.tx(GET_PARAM, [GET_LUT_INFO])
    return rc, d


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cmd, args = sys.argv[1], sys.argv[2:]
    p = PTP()
    try:
        if cmd == 'selftest':  # read-only checks
            rc, _, d = p.tx(0x1001, [])
            print(f'GetDeviceInfo rc=0x{rc:04x} ({len(d)} bytes), USB product 0x{p.dev.idProduct:04x}')
            rc, d = lut_info(p)
            print(f'GetLutInfo rc=0x{rc:04x}')
            bad = 0
            for e in parse_lutinfo(d):
                flag = ''
                if e['type'] == 3 and e['style'] == 65535:
                    flag = '  (no base tag: camera uses V-Log; right for V-Log LUTs, wrong for Standard ones)'; bad += 1
                print(f"{e['pos']:2d} {str(TYPES.get(e['type'], e['type'])):8s} {str(STYLES.get(e['style'], e['style'])):5s} {e['name']}{flag}")
            print(f'selftest done: {bad} user LUT(s) on the V-Log default base (check they are V-Log LUTs)')
        elif cmd == 'list':
            rc, d = lut_info(p)
            print(f'GetLutInfo rc=0x{rc:04x}, {len(d)} bytes')
            open('lutinfo.bin', 'wb').write(d)
            for i in range(0, min(len(d), 4096), 32):
                chunk = d[i:i + 32]
                print(f'{i:05x}  {chunk.hex(" ")}  {"".join(chr(c) if 32 <= c < 127 else "." for c in chunk)}')
            print('raw saved to ./lutinfo.bin')
        elif cmd == 'push':
            path, slot, name = args[0], int(args[1]), args[2]
            if not (0 <= slot <= 40):
                sys.exit('slot must be 0-40')
            blob = open(path, 'rb').read()
            if len(blob) > 9437184:
                sys.exit('file larger than 9 MB')
            _, d = lut_info(p)
            held = next((e['name'] for e in parse_lutinfo(d) if e['pos'] == slot), None)
            if held and '--replace' not in args:
                sys.exit(f'slot {slot} holds "{held}"; add --replace to overwrite it')
            rc, _, _ = p.tx(LUT_START, [slot], name256(name))
            print(f'LutStartFileData slot {slot} "{name}": rc=0x{rc:04x}')
            if rc != 0x2001:
                return
            # USB: LUMIX Lab sends the whole file in ONE LutSendFileData (bufsize = fileSize); 60 KB chunks are its
            # Wi-Fi fallback. Chunking over USB made the camera keep only the last chunk (no header, broken LUT).
            rc, _, _ = p.tx(LUT_SEND, [len(blob)], blob)
            if rc != 0x2001:
                print(f'send rc=0x{rc:04x}, cancelling'); p.tx(LUT_CANCEL, []); return
            print(f'sent {len(blob)} bytes in one transfer: done')
        elif cmd == 'rename':
            slot, name = int(args[0]), args[1]
            rc, _, _ = p.tx(SET_PARAM, [RENAME], struct.pack('<IIH', RENAME, 258, slot) + name256(name))
            print(f'rename slot {slot} -> "{name}": rc=0x{rc:04x}')
        elif cmd == 'sort':
            a, b = int(args[0]), int(args[1])
            rc, _, _ = p.tx(SET_PARAM, [SORT], struct.pack('<IIHH', SORT, 4, a, b))
            print(f'sort {a} -> {b}: rc=0x{rc:04x}')
        elif cmd == 'select':
            slot = int(args[0])
            rc, _, _ = p.tx(SET_REC, [LUT1_REG], struct.pack('<IIH', LUT1_REG, 2, slot))
            print(f'select LUT1 slot {slot}: rc=0x{rc:04x}')
        else:
            sys.exit(__doc__)
    finally:
        p.close()


if __name__ == '__main__':
    main()
