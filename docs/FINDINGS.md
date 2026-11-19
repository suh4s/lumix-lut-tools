# Findings

What we learned building this, including the mistakes, so nobody has to repeat them. Camera: DC-S9, firmware 1.5 → 2.1.

## LUT upload

- **Send the whole file in one `LutSendFileData` (0x9924) over USB.** LUMIX Lab uses 61,440-byte chunks only for Wi-Fi.
  Chunking over USB made the camera keep just the last chunk: no header, base Photo Style "none" (65535), and the LUT
  got applied on V-Log, so switching to it looked broken. One transfer → base 0 (Standard), correct look.
- Uploading to an occupied slot replaces it.
- The library list reports the base Photo Style per LUT; check it after every upload (`lumix_lut.py list`).
- Positions 0 (Vlog_709) and 1 (ARRI 709 sample) can't be sorted into or out of. Deleting leaves an empty record (gap);
  close it by moving the following entries up one at a time.
- The camera shows the name you send (up to 255 ASCII bytes; LUMIX Lab truncates to 95). Card imports keep the file
  name including `.cube`.

## Reading LUTs back (backup)

- **LutGetFileData 0x9921 works**: the command block carries `[0x53000012 u32][slot u8]` (5 bytes, not a u32 param;
  sending it as a data phase gets no answer). Reply: `[0x53000012][size][file length u32][.cube text]`. The camera
  stores the header tags it received (`#GRAINEFFECT`, `#COLORNOISE`) and writes 7-decimal floats, `TITLE ""`.
  `lumix.luts.backup()` / MCP `backup_luts` save the whole library.

## USB modes and properties

- USB mode **PC (Tether)** enumerates as 04da:2382, **LUMIX Lab** as 04da:2387. Both are PTP; Panasonic vendor commands
  work although DeviceInfo doesn't list them. LUT commands are used in LUMIX Lab mode.
- Photo Style: `0x02000010` is read-only (writes return 0x201D); write it via **`0x02000011`** ("StyleMode").
  PTP codes: 0 Standard, 1 Vivid, 2 Natural, 3 Monochrome, 4 Scenery, 5 Portrait, 15 L.Monochrome, 17 Cinelike D2,
  18 V-Log, 49 Real Time LUT. 19–28 = My Photo Style 1–10 on firmware 2.0+ (see My Photo Style below; earlier
  notes read 19 as a reduced-opacity LUT slot and 20–22 as Custom 1–3).
- Image format `0x020000A2`: 3 = RAW, 4 = RAW+JPEG.
- `0x020001FB` (Lut1RegistrationInformation) accepted writes in tether mode but didn't change the applied LUT there.
- Shooting mode (P/A/S/M), custom mode, the photo/video switch, i.Dynamic and Sleep Mode can't be set over PTP in tether mode.
- Firmware 2.1 exposes ~37 more readable properties than 1.5 (0x02000210–0x0200022A and others).
- **Don't use standard PTP property ops (0x1014/0x1015/0x1016) for properties the camera doesn't list** (DeviceInfo
  lists only 0x5001 battery): GetDevicePropValue 0x500E (ExposureProgramMode) gets **no answer**. The camera doesn't
  freeze; the pending transaction blocks the link. **Recovery without replug:** the PTP-over-USB class *Device Reset
  Request* (bmRequestType 0x21, bRequest 0x66) restores it (verified). `lumix/ptp.py` does this automatically on a
  timeout (cancel request → device reset request → clear halts → USB port reset) and warns before such requests.
- **P/A/S/M can't be set over USB.** libgphoto2's `expmode` (0x06000011) is the movie exposure mode; LUMIX Lab has no
  photo exposure-mode setter either. 0x9918 LumixControlCameraMode switches shooting (1) vs gallery/LUT management (2).
- macOS `ptpcamerad` re-grabs the camera between calls; claim the interface with retries. If a transfer is interrupted
  the link can stop answering: the device reset request (0x66) recovers it, replug as a last resort.
- `gphoto2 --capture-image-and-download` deletes the shot from the card after download.

## Camera behaviour worth knowing

- **Per-slot exposure offset:** with a LUT slot selected, the camera metered about +1⅓ EV brighter than Standard at the
  same compensation setting (another LUT entry had −1⅓). It's stored with the slot, not in the `.cube`. Watch zebras.
- The maker note records the applied LUT: exiftool tags `LUT1Name`, `LUT1Opacity`, `MonochromeGrainEffect`,
  `IntelligentD-Range`. Use them to verify which look a file was shot with.
- Camera Standard JPEG ≈ LightCraft default render (~4/255).

## Making LUTs that match a RAW preset

- Presets are tuned for RAW; applying a preset to JPEG-like colour grids gave a flat, blotchy look ~26/255 off. Fit
  the LUT from RAW pairs instead (default render → preset render): held-out error 1.5–2.3/255 on most presets.
- **Geometry pitfall:** if the default render applies lens correction and the preset render doesn't, the frames don't
  line up and the fit learns noise (16/255 instead of 1.5). Switch lens correction off in both renders.
- **Local tone is not LUT-able:** presets with strong Highlights/Shadows (e.g. −70/+53) or clarity stay 4–5/255 off.
  Balanced sampling doesn't help. Camera i.Dynamic Range (High) does part of that work locally and brought those
  presets from ~5 to ~4/255; fit those LUTs for i.Dynamic High.
- Refining a LUT directly on a single tethered scene overfits (held-out error went up); keep it to a sanity check.
- "Bright" variants: lift midtones in a luminance pre-curve with a soft shoulder (white unchanged) instead of raising
  exposure, so highlights don't clip.
- **Strip absolute white balance** from presets (Kelvin/tint): a LUT can't set a colour temperature, the fit turns it
  into a fixed cast (here cool/magenta). Keep WB as shot so the camera's AWB decides; `settings/nospatial.json` does this.
- **Grain works via the `.cube` header** (verified, DC-S9 2.1): header `TITLE "…"`, `#LUMIXPHOTOSTYLE STD`,
  `#GRAINEFFECT L60`, `LUT_3D_SIZE 33`, data (no DOMAIN lines), uploaded over USB in one transfer. Shots then record
  maker note `MonochromeGrainEffect = 5` (was Off) and show visible grain at 100%. L20 was too faint to notice.
  The library list (GetLutInfo) reports 0 in the grain/noise/sharpness fields regardless, so check a shot, not the list.
  **`#COLORNOISE L0` verified too:** with it the camera's own colour-noise setting (0x020001F2) switched to off when
  the LUT was uploaded/active, and shots record `MonochromeGrainEffect` 3 (High grain, no colour noise) instead of 6.
  The camera loads these tags into its settings. Without the tag, High grain inherits the camera's colour-noise
  setting (colour speckles on B&W looks). `#SHARPNESS`, `#NOISEREDUCTION`: same syntax, not verified yet.
- Grain strengths: L1–33 Low, L34–66 Standard, L67–100 High; no size control. L20 was hard to see, L50–60 clear,
  L85 strong. Maker note `MonochromeGrainEffect`: 1/2/3 = Low/Standard/High, 4/5/6 = the same with colour noise.
- More Rec properties found by testing (tether mode): 0x020001F2 colour noise on/off, 0x020001F3 LUT 1 on/off,
  0x020001F9 LUT opacity (10–100).
- **Active LUT selection is not exposed over PTP** (as far as tested; also tried: select style 0x02000223 + My Photo
  Style edit session + 0x020001FB, on style 49 and on MY PHOTO STYLE 1 — accepted, ignored): switching the LUT on the camera changes none of
  the single properties nor the 0x020001F0/0x02000220/0x02000010 blocks; writing 0x020001FB (alone or inside the
  My Photo Style edit session 0x9419 StartEdit/EndEdit) is accepted but ignored; also with 0x020001F3 (LUT 1 on/off)
  re-applied after it. **LUMIX Lab 3.1.0 offers no way to switch the active LUT either**: its UI texts only cover
  transferring LUTs and placing them in the library ("the settings are applied the next time you select this LUT" on
  the camera). Treat active-LUT selection as camera-only.
- LUT upload/rename/sort/delete also work in **PC (Tether)** USB mode (04da:2382); no need to switch to LUMIX Lab mode.
  In tether mode GetLutInfo records are **262 bytes** (id, name, style, type) instead of 272 (no grain/noise/visibility
  fields); the parser detects it from the size field.
- `pipeline/add_grain.py IN OUT LEVEL` writes a camera LUT with grain (colour noise off by default). Standard (L50) is a
  good default for colour looks; High (L85) for heavy B&W.
- A `.cube` without a base tag is applied on V-Log. That's right for V-Log LUTs (ARRI 709_Legacy, S5II Legacy packs)
  and wrong for Standard ones; `lumix_lut.py selftest` lists which LUTs sit on that default.

## My Photo Style (firmware 2.0+, read-only probe on a DC-S9 2.1, `control/mps_probe.py`)

- Ten slots, Photo Style codes **19–28**. Names live in `0x0200022A`: records of `[u16 code][23 B ASCII name]`, the first
  record being code 49 with an empty name; renaming a slot in camera or LUMIX Lab shows up here ("STREETWISE MINE").
- `0x02000220` (writable twin `0x02000221`) = which slots the Photo Style menu shows, `[u16 code][u16 shown]` × 10;
  default 19–22 shown, 23–28 hidden. `0x02000222` = the same for the built-in styles (19 entries, code 48 hidden).
- **A shown slot is selectable over USB**: the allowed list of `0x02000011` (Photo Style write) included 19–22 and
  not 23–28. So code can switch the camera between Real Time LUT and up to ten My Photo Styles.
- `0x02000223` (select setting) = 49 now, allowed 19–28; `0x02000224` (registration) = 0.
- With Real Time LUT active the LUT block `0x020001F0–FC` reads sentinel values (65535/65534/65533), so a slot's
  contents (base style, LUT 1/2, strength) are not readable without selecting or editing that slot.

### Write test, run 1 (`control/mps_write_test.py`, 2026-11-18): writes work, the image effect is still unmeasured

- **A hidden slot can't be selected for editing.** StartEdit (0x9419 0x03010041), then `0x02000223` = 28 (slot 10,
  hidden) → 0x201D, and the session sat on code 19 (slot 1); the LUT writes that followed were refused (0x201D).
  After showing the slot (`0x02000221` = `28 | 1 << 16`), selecting it for editing worked and `0x020001FB` (LUT 1
  registration) and `0x020001F3` (LUT 1 on) were accepted and read back. Show first, then edit; and check
  `0x02000223` reads the slot before writing anything.
- **Photo Style writes are refused for a while after a style change.** With a My Photo Style just selected,
  `0x02000011`, `0x02000221` and `0x02000223` returned 0x201D on immediate writes; the same writes a few seconds later
  succeeded. Retry 0x201D like 0x2019.
- With the slot active as Photo Style, its LUT settings read real values inside an edit session (LUT 1 off = 0,
  registration 65535 = none, strength 65534); with another style active they read sentinels.
- 0x9419 Reset (0x03010044, `[u16 code]`) answers OK.
- Whether the registered LUT changes the picture: not measured yet (live view was flat, lens cap on). Rerun on a lit,
  colourful scene; pass = LUT A, LUT B and LUT off give clearly different frames.

## Firmware

- Panasonic only offers the newest firmware; update from the SD card (`.bin` on the card root, Setup → Firmware).
  Remove macOS `._` sidecar files from the card so the camera doesn't see a second `.bin`.
- 2.0 added LUMIX Lab wired connection and LUT extras (grain/colour noise for photos, sharpness, NR).

## Focus and streaming (DC-S9, firmware 2.1)

- **Manual focus works over USB**: ManualFocusDrive 0x9416, param 0x03010011, data `[u32 0x03010011][u32 2][u16 step]`,
  step 0 stop, 1 far fast, 2 far slow, 3 near slow, 4 near fast (as libgphoto2). The motor runs until `stop`.
  Only in focus mode MF; otherwise 0x201D.
- **Focus mode** 0x02000071 (0x02000070 reads the same): allowed 4/5/3; 5 = AFC (camera screen), 3 = MF (verified by
  MF drive), 4 presumably AFS. AF area mode 0x02000072 (allowed 2, 21, 15, 17, 14, 0; labels not mapped).
  Focus point position: no USB command found (LUMIX Lab has none either).
- **No USB video class (webcam) mode**: the S9's USB Mode menu offers Select on connection, PC(Storage), PC(Tether)
  (and LUMIX Lab since 2.x). 0x9707 is "4K/6K Photo cutting get stream", not a video stream. The PTP live view
  (≤1280x960, 30 fps, no audio) is the only video over USB.

## Touch AF (from LUMIX Tether 2.8's libLmxptpif, verified accepted on a DC-S9)

- Op 0x9416 (Rec Ctrl, same as MF drive), data `[u32 code][u32 size][u16 params]`:
  0x030000A1 AF area start (x, y), 0x030000A2 move (x, y), 0x030000A3 reset, 0x030000A4 end, 0x030000A5 clear.
  Coordinates 0..1000. Start + End, then AF one-shot (0x9405 0x03000024) = touch AF.
- GetAFInfo: op 0x9401 param 0x01000010 → sections 0x01000011–0x0100001D (0x01000013 and 0x01000017 start with
  1000, 1000 = coordinate space). Which section holds the live AF frame is not mapped yet (0x01000013 [10]/[12]/[14]/[15]
  looked like a frame but didn't follow touches).
- Aperture: the camera lists every 0.1 f-stop; real steps are the 1/3-stop scale.

## MF assist (from LUMIX Tether, verified on a DC-S9)

- Op 0x9416 with 0x03000051 start (mode, 0, x, y, 0), 0x03000052 set ratio (3 params), 0x03000053 move (x, y),
  0x03000054 end, 0x03000055 one-point, 0x03000056 zoom start (4 params); u16 params, 0..1000.
- Start mode 2 = magnified live view at (x, y) (live view frames change; start at another point shows another area,
  move to a point = same view as starting there). Mode 0 accepted with no visible change; 1, 3–5 refused (0x201D).
- In MF the AF area commands are refused (0x201D): touch in MF = MF assist.
