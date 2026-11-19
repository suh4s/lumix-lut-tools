"""lumix-mcp: MCP server for Panasonic LUMIX cameras over USB (tested: DC-S9, firmware 2.1).

Run: `lumix-mcp` (stdio). Camera: USB mode PC (Tether) (or LUMIX Lab), connected by USB.
Safety: firmware operations are blocked in the transport; delete/replace need confirm=true; every setting change is
recorded so restore_settings can undo it.
"""
import os

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from . import capture as cap
from . import luts, settings
from .ptp import Camera, CameraError

server = MCPServer(
    name='lumix-mcp',
    title='LUMIX camera control',
    instructions=(
        'Controls a Panasonic LUMIX camera on USB. Start with camera_status. LUTs live in the camera LUT Library '
        '(positions 0-1 are fixed built-ins). The active LUT can only be chosen on the camera; capture reports which LUT '
        'was applied. Ask the user before deleting or replacing LUTs. Use restore_settings to undo setting changes.'),
    version='0.1.0',
)

READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False)

_changes = []  # (key, old value) in order, for restore_settings


def _set(cam, key, value):
    old, new = settings.write(cam, key, value)
    if old != new:
        _changes.append((key, old))
    return {'setting': key, 'old': old, 'new': new}


@server.tool(annotations=READ)
def camera_status() -> dict:
    """Model, firmware, USB mode and current settings (Photo Style, white balance, ISO, EV, aperture, LUT opacity)."""
    with Camera() as cam:
        info = cam.device_info()
        info.pop('serial', None)
        return {**info, 'usb_mode': cam.usb_mode, 'settings': settings.status(cam), 'pending_restore': len(_changes)}


@server.tool(annotations=READ)
def list_luts() -> list[dict]:
    """The camera's LUT Library in order: position, name, base Photo Style, type. on_vlog_default=true means the LUT
    has no base tag and is applied on V-Log (fine for V-Log LUTs, wrong for Standard ones)."""
    with Camera() as cam:
        return luts.library(cam)


@server.tool(annotations=DESTRUCTIVE)
def upload_lut(path: str, name: str, position: int | None = None, grain: int | None = None,
               color_noise: int | None = None, base_style: str = 'STD', confirm: bool = False) -> dict:
    """Upload a .cube (max 33 points) into the LUT Library. position=None uses the first free slot.
    grain: 1-33 low, 34-66 standard, 67-100 high (omit for none); color_noise: 0 off, 1-100 on.
    base_style: 'STD'/'NAT'/... for LUTs made on that style; 'VLOG' for V-Log LUTs (creator packs, Resolve exports):
    no style tag is written and the camera applies the LUT on V-Log.
    Replacing an occupied position needs confirm=true."""
    text = open(os.path.expanduser(path)).read()
    blob = luts.cube_with_tags(text, style=base_style, grain=grain, color_noise=color_noise, title=name).encode('ascii')
    with Camera() as cam:
        names = luts.names_by_position(cam)
        if position is None:
            free = [i for i in range(2, 41) if i >= len(names) or not names[i]]
            if not free:
                raise CameraError('LUT Library is full (41 entries).')
            position = free[0]
        elif position < len(names) and names[position] and not confirm:
            raise CameraError(f'Position {position} holds "{names[position]}". Pass confirm=true to replace it.')
        entry = luts.upload(cam, blob, position, name)
        return {'uploaded': entry, 'bytes': len(blob)}


@server.tool(annotations=READ)
def download_lut(name: str, path: str) -> dict:
    """Read a LUT back from the camera and save it as a .cube file."""
    with Camera() as cam:
        text = luts.download(cam, luts.find(cam, name))
    dst = os.path.expanduser(path)
    open(dst, 'w').write(text)
    return {'saved': dst, 'bytes': len(text), 'header': text.split('\n')[:5]}


@server.tool(annotations=READ)
def backup_luts(directory: str = '~/Pictures/lumix-mcp/lut-backup') -> list[str]:
    """Download every user LUT from the camera into a folder (position_name.cube)."""
    with Camera() as cam:
        return luts.backup(cam, os.path.expanduser(directory))


@server.tool(annotations=WRITE)
def rename_lut(name: str, new_name: str) -> list[str]:
    """Rename a LUT in the library. Returns the library names by position."""
    with Camera() as cam:
        luts.rename(cam, luts.find(cam, name), new_name)
        return luts.names_by_position(cam)


@server.tool(annotations=WRITE)
def move_lut(name: str, position: int) -> list[str]:
    """Move a LUT to a position (2-40; 0 and 1 are fixed). Returns the library names by position."""
    with Camera() as cam:
        return luts.move(cam, name, position)


@server.tool(annotations=DESTRUCTIVE)
def delete_lut(name: str, confirm: bool = False) -> list[str]:
    """Delete a LUT from the camera (the gap is closed). Needs confirm=true; keep a copy of the .cube first."""
    if not confirm:
        raise CameraError(f'Deleting "{name}" needs confirm=true.')
    with Camera() as cam:
        return luts.delete(cam, name)


@server.tool(annotations=WRITE)
def write_lut_file(source: str, destination: str, grain: int | None = None, color_noise: int | None = 0,
                   base_style: str = 'STD', title: str | None = None) -> dict:
    """Write a camera-ready .cube with LUMIX header tags (base style, grain, colour noise) without touching the camera."""
    text = luts.cube_with_tags(open(os.path.expanduser(source)).read(), style=base_style, grain=grain,
                               color_noise=color_noise, title=title)
    dst = os.path.expanduser(destination)
    open(dst, 'w').write(text)
    return {'written': dst, 'header': text.split('\n')[:5]}


@server.tool(annotations=WRITE)
def set_photo_style(style: str) -> dict:
    """Photo Style: standard, vivid, natural, monochrome, scenery, portrait, l_monochrome, cinelike_d2, vlog,
    my_photo_style_1-10 (slots shown in the camera menu), real_time_lut (the LUT chosen on the camera)."""
    if style not in settings.PHOTO_STYLES:
        raise CameraError(f'Unknown style. Use one of {sorted(settings.PHOTO_STYLES)}')
    with Camera() as cam:
        return _set(cam, 'photo_style', settings.PHOTO_STYLES[style])


@server.tool(annotations=WRITE)
def set_white_balance(mode: str, kelvin: int | None = None) -> list[dict]:
    """White balance mode (auto, auto_warm, auto_cool, daylight, cloudy, shade, tungsten, fluorescent, flash, kelvin).
    With mode=kelvin pass kelvin (e.g. 5300). Higher Kelvin = warmer image."""
    if mode not in settings.WB_MODES:
        raise CameraError(f'Unknown mode. Use one of {sorted(settings.WB_MODES)}')
    with Camera() as cam:
        out = [_set(cam, 'white_balance', settings.WB_MODES[mode])]
        if kelvin is not None:
            out.append(_set(cam, 'kelvin', int(kelvin)))
        return out


@server.tool(annotations=WRITE)
def set_exposure(iso: str | int | None = None, exposure_comp_ev: float | None = None,
                 aperture_f: float | None = None) -> list[dict]:
    """ISO ('auto' or a value like 800), exposure compensation in stops (1/3 steps), aperture f-number (e.g. 5.6).
    Aperture works in A and M modes; the mode itself is set on the camera."""
    out = []
    with Camera() as cam:
        if iso is not None:
            out.append(_set(cam, 'iso', settings.ISO_AUTO if str(iso).lower() == 'auto' else int(iso)))
        if exposure_comp_ev is not None:
            out.append(_set(cam, 'exposure_comp', settings.ev_encode(exposure_comp_ev)))
        if aperture_f is not None:
            out.append(_set(cam, 'aperture', round(aperture_f * 10)))
    return out


@server.tool(annotations=WRITE)
def set_image_format(image_format: str) -> dict:
    """Image format: raw or raw+jpeg."""
    if image_format not in settings.IMAGE_FORMATS:
        raise CameraError(f'Use one of {sorted(settings.IMAGE_FORMATS)}')
    with Camera() as cam:
        return _set(cam, 'image_format', settings.IMAGE_FORMATS[image_format])


@server.tool(annotations=WRITE)
def set_lut_options(opacity: int | None = None, color_noise: bool | None = None) -> list[dict]:
    """Active LUT opacity (10-100 in steps of 10) and the camera's colour-noise switch."""
    out = []
    with Camera() as cam:
        if opacity is not None:
            out.append(_set(cam, 'lut1_opacity', int(opacity)))
        if color_noise is not None:
            out.append(_set(cam, 'color_noise', int(color_noise)))
    return out


@server.tool(annotations=WRITE)
def restore_settings() -> list[dict]:
    """Undo every setting change made through this server, newest first."""
    done = []
    with Camera() as cam:
        while _changes:
            key, old = _changes.pop()
            if old is not None:
                settings.write(cam, key, old)
                done.append({'setting': key, 'restored': old})
    return done


@server.tool(annotations=WRITE)
def capture(output_dir: str = '~/Pictures/lumix-mcp', basename: str | None = None) -> dict:
    """Take a picture and download it (gphoto2 removes it from the card). Returns file paths and what the camera
    applied: LUT name, grain, Photo Style, white balance, ISO, shutter, aperture."""
    return cap.capture(os.path.expanduser(output_dir), basename)


@server.tool(annotations=READ)
def selftest() -> dict:
    """Read-only health check: connection, device info, settings, LUT Library."""
    with Camera() as cam:
        info = cam.device_info()
        info.pop('serial', None)
        lib = luts.library(cam)
        return {'device': info, 'usb_mode': cam.usb_mode, 'settings': settings.status(cam), 'luts': len(lib),
                'luts_on_vlog_default': [e['name'] for e in lib if e['on_vlog_default']]}


def main():
    server.run('stdio')


if __name__ == '__main__':
    main()
