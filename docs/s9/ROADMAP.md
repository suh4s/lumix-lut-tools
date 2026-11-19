# S9 control roadmap

As of 2026-11-18. Camera: LUMIX DC-S9, firmware 2.1. Branch `s9-control` of `suh4s/lumix-lut-tools`.

The next three builds are the camera broker, settings-as-code over USB, and **Looks** (My Photo Style slots driven
from code). Looks is new since the original plan: a read-only probe showed that My Photo Style slots shown in the
menu can be selected over USB. That is the most promising way around "code cannot choose the active LUT".

## How the code reaches the camera

Everything travels over one USB link as PTP plus Panasonic vendor commands, decoded from LUMIX Lab for Android and
LUMIX Tether. Only one program can hold the link at a time.

```text
 Phone /m      LumixOS app     Claude plug-in    CLI tools        LUT pipeline
 (web app)     (live view,     (lumix-mcp,       (lut_batch,      (presets -> LUTs,
     |          OBS feed)       17 tools)         lumix_lut ...)    vlog_preview)
     +------+------+                |                 |                 |
            |                       |                 |                 |
      lumix-server (HTTP, owns live view, token for non-loopback)       |
            |                       |                 |                 |
            +-----------------------+--------+--------+-----------------+
                                             |
          Camera features: settings · luts + batch · liveview · capture (gphoto2 + exiftool)
                                             |
          ptp.py: one USB link, vendor commands, link recovery, firmware commands refused
                                             |
          LUMIX S9 (USB mode PC (Tether) 04da:2382 or LUMIX Lab 04da:2387)
```

`capture` is the one module that goes around `ptp.py`: it calls gphoto2, and gphoto2 cannot shoot in LUMIX Lab USB
mode.

| Status | What |
| --- | --- |
| Working | LUT library (list, upload, rename, reorder, delete, back up, batch load with verification); Photo Style, WB, Kelvin, ISO, EV, aperture, shutter, image format, LUT strength, colour noise; live view 1280×960 at 30 fps; touch AF, MF drive, magnified MF assist; shutter; My Photo Style 1–4 selectable as Photo Style |
| Decoded, not built | My Photo Style editing (fine-tune, LUT 1/2 registration, rename, show/hide); dual native ISO; gallery browse/download (0x9911–0x9917); settings-file export/import (0x9421–0x9423); clock (0x940B) and GPS (0x9411); card status; change events |
| Camera-only | P/A/S/M dial, custom modes C1–C3, photo/video switch, choosing the active Real Time LUT directly, i.Dynamic; no UVC webcam mode; live view ≤ 1280×960 |

## Feature backlog

Effort: S about a day, M a few days, L a week or more.

| Priority | Feature | What you get | Built on | Effort |
| --- | --- | --- | --- | --- |
| P1 | Camera broker | lumix-server owns the USB link; plug-in, CLI, Mac app and phone all go through it and can be used together | server.py command queue | M |
| P1 | Looks (My Photo Style from code) | Ten complete looks (base style + fine-tune + up to two LUTs with strength + grain), chosen from the phone | probe done; write test next | M |
| P1 | Settings-as-code over USB | `profiles/s9/s9_spec.yaml` checked and enforced over USB, drift report after each outing | settings.py, s9check.py | S |
| P1 | Phone remote v2 | /m gains LUT library management, recipes kept on the Mac, Looks picker | /m, luts.py | S |
| P1 | Live LUT trial | Flip through all 41 pack LUTs on one scratch slot with live view on the phone | swap test (pending) | M |
| P1 | Base-ISO guard | Warns when bright light pushes you off the style's native ISO (640 for V-Log / Real Time LUT); suggests ND or a faster shutter | Photo Style + live histogram | S |
| P2 | Look builder for LUT packs | Per-LUT grain, colour noise, sharpness, NR columns in the batch manifest | luts.cube_with_tags | S |
| P2 | Self-calibration | Greengo's camera tests run automatically: ISO ladder (noise), aperture sweep per lens, shutter sweep | capture + image analysis | M |
| P2 | Focus bracketing | N shots with an MF-drive step between them, ready for stacking | MF drive 0x9416 + shutter | M |
| P2 | Night and astro kit | Interval shooting for star stacking, magnified MF assist with peaking for star focus, dark frames | MF assist, shutter, timer | M |
| P2 | Exposure-ramped time-lapse | Day-to-night sequences that adjust shutter and ISO from the live histogram | live view header + settings | M |
| P2 | Shot journal | Every capture logged with LUT, grain, WB, ISO, exposure from the maker note | capture.py + exiftool | S |
| P2 | Gallery over USB | Browse, thumbnails, download without deleting from the card | 0x9911–0x9917 (to verify) | M |
| P2 | Event listener | Camera pushes changes instead of 2-second polling | PTP interrupt endpoint | S |
| P3 | Settings backup over USB | Export/restore the camera settings file without the SD card | 0x9421–0x9423 (to verify) | M |
| P3 | Clock and GPS sync | Camera clock and geotags from the phone | 0x940B, 0x9411 | M |
| P3 | Native iPhone app | Direct USB-C control, no Mac (see PHONE_APP.md) | ImageCaptureCore PTP | L |
| P3 | Wi-Fi and Bluetooth control | Cable-free control the way LUMIX Lab does it | reverse-engineering | L |

## Open tests

1. **LUT-swap test** (`control/lut_swap_test.py`): does rewriting the selected slot change the look without
   re-selecting it? Blocked: in LUMIX Lab USB mode nothing reports the active LUT and gphoto2 cannot shoot. Select
   Vibrant Pop (slot 40) on the camera, then `lut_swap_test.py OUT --slot=40 --yes`.
2. **My Photo Style write test**: edit slot 10 (base V-Log + one LUT), show it in the menu (`0x02000221`), select it
   with `0x02000011`, check live view, restore every value. Plan in FIRMWARE_FEATURES.md.
3. **Native iPhone spike**: one-screen Swift app; find the S9 through ImageCaptureCore and send GetLutInfo (0x9414).
