# lumix-lut-tools

Manage the **Real Time LUT Library of a Panasonic LUMIX S9 from a computer over USB**, without the LUMIX Lab app or an
SD card, plus a pipeline for turning photo-editor presets into camera LUTs that look like the preset on the RAW.

Tested on a DC-S9 with firmware 2.1 on macOS (Apple Silicon). Other LUMIX bodies that support LUMIX Lab's wired
connection probably speak the same protocol, but are untested.

### This fork: S9 control

Branch `s9-control` extends the tools from LUT management to driving the S9 from a Mac, a phone and Claude:
batch LUT loading with verification, a phone remote (`/m`), a read-only probe of firmware 2.0's My Photo Style
slots, my S9 setup kept as code, and a roadmap. Start at [docs/s9/](docs/s9/README.md).

## What works

| Task | Tool |
|---|---|
| Read-only check of camera and library | `control/lumix_lut.py selftest` |
| List the LUT Library (names, base Photo Style, type) | `control/lumix_lut.py list` |
| Upload a `.cube` into a slot (occupied slots need `--replace`) | `control/lumix_lut.py push FILE.cube SLOT "Name" [--replace]` |
| Load a whole pack from a manifest (plan, back up, upload, verify) | `control/lut_batch.py plan\|load MANIFEST` |
| Rename / reorder | `control/lumix_lut.py rename SLOT "Name"`, `control/lut_reorder.py order.txt --start=2` |
| Read device info in any USB mode | `control/devinfo.py` |
| Tethered shooting, Photo Style / format / WB / EV over USB | `control/lumixctl.py`, `control/tether_pairs.py` |
| Add grain (no colour noise) to any LUT | `pipeline/add_grain.py my.cube my_grain.cube 50` |
| Preset → 33-point camera LUT, evaluation sheets | `pipeline/` (needs LightCraft, see below) |

## lumix-mcp (MCP server)

Lets an AI assistant (Claude Code, Claude Desktop, any MCP client) drive the camera: list/upload/rename/move/delete LUTs,
write grain into LUTs, set Photo Style, white balance, ISO, exposure, aperture, image format, LUT opacity, take pictures
and report what the camera applied (LUT, grain, WB).

```sh
python3 -m venv .venv && .venv/bin/pip install -e .     # needs libusb (brew install libusb); capture needs gphoto2 + exiftool
claude mcp add lumix-mcp -- "$PWD/.venv/bin/lumix-mcp"   # Claude Code; other clients: command = .venv/bin/lumix-mcp (stdio)
```

| Tool | |
|---|---|
| `camera_status`, `selftest`, `list_luts` | read only |
| `upload_lut` (grain/colour noise optional), `rename_lut`, `move_lut`, `delete_lut` | LUT Library; delete/replace need `confirm=true` |
| `download_lut`, `backup_luts` | read LUTs back from the camera as `.cube` |
| `write_lut_file` | camera-ready `.cube` with header tags, no camera needed |
| `set_photo_style`, `set_white_balance`, `set_exposure`, `set_image_format`, `set_lut_options` | verified by read-back |
| `restore_settings` | undo every setting change made through the server |
| `capture` | shoot + download, returns files and the applied LUT/grain/WB/ISO |

Not possible over USB (as far as tested): choosing the active LUT, P/A/S/M, custom modes, i.Dynamic. Firmware
operations are blocked in the transport. Tests: `python tests/test_offline.py`, with a camera `python tests/live_mcp_test.py [--write]`.

## LumixOS (macOS app) and lumix-server

`apps/LumixOS` is a SwiftUI app: low-latency live view (1280x960 @ 30 fps, newest frame only) with a camera-style
HUD over the picture: WB/Kelvin, Photo Style (+ LUT opacity), quality on top; mode, aperture, EV, ISO, shutter,
remaining shots and battery at the bottom; grid, centre cross, level line, optional histogram. Hover a value to open
its options, click to set it. Focus: click the picture for touch AF (moves the AF area there and focuses; in MF it opens the
magnified MF assist there instead, click/Esc closes), focus peaking (p, Low/Mid/High, 4 colours), AF (f), focus
mode AFS/AFC/MF in the HUD, an MF ring on the right (drag up = far; grabbing it switches to MF), `[` `]` keys
(`{` `}` fast) or the scroll wheel. Aperture lists real 1/3 stops only. Keys: space shutter, g grid, l level,
h histogram, s settings panel. **Camera** menu: send the live view into **OBS Virtual Camera** (no signing needed: LumixOS
feeds the sink of OBS's own camera extension; OBS needn't run), install the own "LumixOS Camera" extension (needs an
Apple Developer team, see apps/LumixOS/README.md) or copy the stream URL for OBS.
Errors (no camera, unplugged, camera held by another program, live view refused, service crash) are shown on the
picture with a Retry button; the app restarts the service if it dies (log: `~/Library/Logs/LumixOS/server.log`).

The app starts `python -m lumix.server` (127.0.0.1:8765; `/stream`, `/stream?raw=1`, `/state`, `/set`, `/af`,
`/shutter`, `/undo`, `/size`, `/focus`; commands answer `{ok, message|error}`) from this repo's `.venv` (or `LUMIX_REPO`).
Build: `apps/LumixOS/build_app.sh`. A Tk version: `python -m lumix.monitor`.
The level line assumes LevelInfo (0x17000005) carries roll in 0.1° (not yet verified by tilting).

## Phone remote (/m)

`lumix-server` also serves a phone UI at `/m`: live view, tap-to-focus, shutter, manual-focus nudges, every setting
the camera offers, one-tap **recipes** (named sets of settings kept on the phone) and a pre-shot checklist. The camera
stays on the Mac's USB port; the phone talks to the Mac over Wi-Fi or Tailscale.

```sh
python -m lumix.server --host 0.0.0.0 --port 8767        # prints http://<this-mac>:8767/m?t=<token>
```

Open the printed URL once on the phone (Safari → Share → Add to Home Screen). Any host other than loopback requires
the token (generated if `--token`/`LUMIX_TOKEN` is not given); the page stores it and the server sets a cookie.

## Quick start (LUT upload)

1. Camera: **Setup → USB Mode → PC (Tether)** (or LUMIX Lab), connect by USB.
2. `pip install -r requirements.txt` (pyusb) and `brew install libusb`.
3. ```sh
   python3 control/lumix_lut.py list
   python3 control/lumix_lut.py push my_look.cube 20 "My Look"
   ```
   The `.cube` needs `#LUMIXPHOTOSTYLE STD` (or another base) on line 2 and at most 33 points.
   Optional header lines: `#GRAINEFFECT L50`, `#COLORNOISE L1`, `#SHARPNESS L60`, `#NOISEREDUCTION L40` (L0–L100).

On macOS the system camera service `ptpcamerad` grabs the camera; the tools kill it before connecting.

## Batch load a LUT pack

`control/lut_batch.py` loads many LUTs in one pass: it reads a manifest, plans free positions, prepares every file
before writing anything, optionally backs up the library, uploads, and checks each entry by reading the library back.

```sh
# manifest.csv: path,name[,position]   (paths relative to the manifest; # starts a comment)
#   FE01_Subtle_Contrast_1.cube,Subtle Contrast 1
#   AE07_Cine_Film_1.cube,Cine Film 1,12
python3 control/lut_batch.py list
python3 control/lut_batch.py plan manifest.csv --base=VLOG
python3 control/lut_batch.py load manifest.csv --base=VLOG --backup=~/lut-backup --yes
```

`--base` decides the Photo Style each LUT sits on:

| `--base` | Header written | Use for |
|---|---|---|
| `VLOG` (default) | no `#LUMIXPHOTOSTYLE` tag; the camera applies the LUT on V-Log | packs built for V-Log input |
| `STD`, `NAT`, ... | `#LUMIXPHOTOSTYLE <style>` | creative LUTs built for a Photo Style |
| `keep` | leaves the file's own tag (or its absence) | files already prepared for the camera |

Positions 0 and 1 are fixed; new LUTs go into free positions 2–40 in order. An occupied position is refused unless
`--replace`; a name already in the library is refused. Nothing is written without `--yes`.
The same logic is available from Python as `lumix.batch` and is tested offline in `tests/test_batch.py`.

## Docs

- [docs/PROTOCOL.md](docs/PROTOCOL.md): PTP vendor commands, LUT Library protocol, property codes, `.cube` tags
- [docs/FINDINGS.md](docs/FINDINGS.md): what we learned the hard way (pitfalls, camera behaviour, measurements)
- [docs/METHOD.md](docs/METHOD.md): how the preset → LUT pipeline works and why
- [docs/CODES.md](docs/CODES.md): all command/parameter code tables
- [docs/APP_COMMANDS.md](docs/APP_COMMANDS.md): every request LUMIX Lab builds, with data layout
- [docs/SAFETY.md](docs/SAFETY.md): what these tools never do
- [docs/DEVNOTES.md](docs/DEVNOTES.md): how it was built, including dead ends
- [docs/REPRODUCE.md](docs/REPRODUCE.md): verify the protocol against a newer LUMIX Lab yourself
- [docs/FIRMWARE.md](docs/FIRMWARE.md): official firmware link and checksums
- [docs/TESTED.md](docs/TESTED.md): tested cameras (add yours)
- [docs/s9/](docs/s9/README.md): S9 control roadmap, firmware 2.x features, phone app, techniques, session log
- [docs/scans/](docs/scans/): raw property dumps, firmware 1.5 and 2.1, My Photo Style probe
- [examples/](examples/): demo LUTs, start with `identity_std.cube`

Offline tests: `python3 tests/test_parse.py`, `python3 tests/test_batch.py`.

## Safety

The tools never use the firmware channel (PTP 0x9606/0x9607) and refuse to. LUT operations only touch the LUT Library.
Still: this is unofficial, use at your own risk, and keep your own copies of every LUT.

## Pipeline requirements

The `pipeline/` scripts render RAWs with LightCraft's command-line renderer (`lightcraft-cli`). Set `LIGHTCRAFT_CLI` and
`LIGHTCRAFT_LIBRARY` if they aren't on the default paths. Python: numpy, scipy, pillow. exiftool for maker notes.

## How the protocol was found

From the PTP command tables and request builders in Panasonic's LUMIX Lab Android app (3.1.0), for interoperability,
then verified against a camera. No Panasonic code, assets or firmware are included in this repository.

## Licence

MIT, see [LICENSE](LICENSE). Not affiliated with Panasonic. LUMIX is a trademark of Panasonic Holdings Corporation.

## Credits

- [libgphoto2](https://github.com/gphoto/libgphoto2): Panasonic PTP property codes and tethering basics
- [sapoepsilon/lumix-remote](https://github.com/sapoepsilon/lumix-remote): Wi-Fi `cam.cgi` notes
- [t0saki/lumix-lut-converter](https://github.com/t0saki/lumix-lut-converter): LUMIX `.cube` format and V-Log/Standard base behaviour
