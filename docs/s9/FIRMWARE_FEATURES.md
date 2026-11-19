# What firmware 2.0 and 2.1 opened, and what code can do with it

Firmware 2.0 (June 2026) is the one that matters for code: Real Time LUT reads image-quality tags from the LUT, and
My Photo Style became editable from LUMIX Lab, over Wi-Fi or a USB cable. Firmware 2.1 (October 2026) adds
camera-side and video features that code does not need to touch.

## Firmware 2.0

| Feature | What it does | Code today | Enhancement |
| --- | --- | --- | --- |
| LUT image-quality tags | With Real Time LUT the camera reads grain, colour noise, sharpness and NR stored in the LUT; grain and colour noise apply to photos only | `#GRAINEFFECT` and `#COLORNOISE` written and verified; `#SHARPNESS`, `#NOISEREDUCTION` same syntax, not yet verified | Look builder: per-LUT tag columns in the `lut_batch` manifest |
| My Photo Style editing | Ten slots, each a base Photo Style plus fine-tuning plus up to two LUTs with strength | Read-only probe (`control/mps_probe.py`): names, shown/hidden, selectable codes | **Looks**: write slots from code, select them over USB |
| Wired LUMIX Lab connection | LUTs, images and My Photo Style edits over USB from a phone | `lumix-lut-tools` uses the same USB mode (04da:2387) | Evidence that a phone can drive the S9 over USB: see PHONE_APP.md |
| Firmware update from the app | Lab downloads firmware and the camera installs it | Firmware commands (0x9606/0x9607) are refused by `ptp.py` | None, on purpose |

## Firmware 2.1

- Ten more Lens Fn assignments (aspect ratio, video E-stabilization, Boost I.S., video image area, focus ring control,
  focus limiter, hybrid zoom, crop zoom, focus peaking, video record start/stop): set on the camera, only on lenses
  with that button.
- MP4 (Lite) 10-bit 4:2:0 up to 4K 60p/50p and FHD 120p/100p: video only.
- LUMIX Flow 1.7 support: a phone as an external monitor with storyboard and review tools, for video.

## My Photo Style probe (read-only, 2026-11-18)

Raw result: `docs/scans/mps_probe_fw2.1.json`. Findings are also in `docs/FINDINGS.md`.

| Slot | Code | Name on this camera | In the menu | Selectable over USB |
| --- | --- | --- | --- | --- |
| 1 | 19 | STREETWISE MINE | yes | yes |
| 2 | 20 | MY PHOTO STYLE 2 | yes | yes |
| 3 | 21 | MY PHOTO STYLE 3 | yes | yes |
| 4 | 22 | MY PHOTO STYLE 4 | yes | yes |
| 5–10 | 23–28 | MY PHOTO STYLE 5–10 | no | no |

- Photo Style codes selectable through `0x02000011`: 0, 1, 2, 44, 18, 4, 5, 3, 15, 17, 45, 50, 41, 42, 14, 40, 49 and
  19–22. Names of 14, 16, 40–45, 48, 50 still to map (shoot each once and read the maker note).
- With Real Time LUT active, the LUT block `0x020001F0–FC` returns sentinel values, so a slot's contents can only be
  read by selecting or editing it.
- `settings.PHOTO_STYLES` now names 19–28 `my_photo_style_1` … `my_photo_style_10` (it previously called 20–22
  custom1–3). The phone page lists the selectable ones automatically.

## Write test for Looks (`control/mps_write_test.py`; run 1 inconclusive)

Safety rules from `docs/SAFETY.md` apply: only settings the camera menu also offers, old values recorded first and
restored after.

1. Record everything the probe reads, plus the current Photo Style.
2. On slot 10 (unused, hidden): StartEdit (0x9419 0x03010041), select slot 10 (`0x02000223` = 28), set base style
   V-Log (`0x020001F7`), register LUT 1 = Vibrant Pop (`0x020001FB` = 40), LUT 1 on (`0x020001F3`), strength 100
   (`0x020001F9`), EndEdit (0x03010042).
3. Show slot 10 in the menu (`0x02000221`) **before** editing it (a hidden slot can't be selected for editing), then
   set Photo Style = 28 (`0x02000011`).
4. Pass: live view takes on Vibrant Pop's look (compare to Real Time LUT on slot 40). Fail: values accepted but no
   change, as the earlier attempt recorded in FINDINGS.md.
5. Restore: Photo Style back to 49, slot 10 hidden, slot 10 reset (0x03010044) or its recorded values written back.

Run 1 (2026-11-18): the slot writes were accepted once the slot was shown, but live view was flat (lens cap on), so
the look comparison measured nothing. The camera refused Photo Style writes for a few seconds after switching to the
slot, so the script's restore failed; a retry restored everything (Photo Style 49, slot 10 hidden, same active LUT).
The script now retries refused writes, shows the slot first, and stops if the edit session is on any other slot.
Details in `docs/FINDINGS.md`.
