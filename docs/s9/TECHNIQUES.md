# Techniques and customizations for the S9

Techniques gathered from courses I own, mapped to the S9 and to what code here can drive. "Backlog" points to
[ROADMAP.md](ROADMAP.md). Course material itself is not in this repo; these are my notes on it.

## Sources

| Teacher | Material | For the S9 |
| --- | --- | --- |
| Dustin Armstrong | LUMIX PRO: Master Your Camera (10 chapters); Photo Styles cheat sheet; LUT packs | Direct: every chapter is LUMIX |
| Hudson Henry | Landscape & Travel course; Milky Way & Stars outline; night gear; no-parallax point; Approaching The Scene (ATS) videos | High: exposure, light, HDR, panoramas, time-lapse, night |
| Mike Browne | 7 Building Blocks of Photography; Ultimate Beginners Course; Lenses Exposed | High: a way to think before each shot |
| John Greengo | Fundamentals of Photography (with learning projects); Travel Photography | High for fundamentals and self-tests |
| CreativeLive | Composition, commercial portraiture, filmmaking courses | Medium: composition and video |
| Chase Jarvis | Photography Challenges | Medium: creative drills |
| Gavin Gough | Natural light, travel photography, workflow | Low for camera control |

## The one that matters most here

Real Time LUT shoots on V-Log, whose native ISOs are **640 and 4000** (Dustin's cheat sheet). The S9 still offers
ISO 100 and up in that mode. Whether going below 640 costs quality is what the ISO-ladder self-test would measure.
Until then: in bright light, reach for ND or a faster shutter before lowering ISO.

| Photo Style | Native ISOs |
| --- | --- |
| Standard, Vivid, Natural, Monochrome, L.Monochrome | 100, 640 |
| Cinelike D2 / V2 | 200, 1250 |
| HLG | 400, 2500 |
| V-Log, Real Time LUT | 640, 4000 |

## Techniques

| Theme | Technique | From | On the S9 | Code |
| --- | --- | --- | --- | --- |
| Exposure | Decide how light or dark the picture should be; judge by histogram, protect highlights | Mike Browne (block 6), Greengo | Histogram and highlight warning; each LUT slot can shift metering ~1⅓ EV | Yes: /m histogram, exposure compensation |
| Exposure | Native ISO depends on Photo Style (table above) | Dustin, cheat sheet | Native 640 with Real Time LUT; ISO 100+ still offered | Yes: base-ISO guard (backlog) |
| Exposure | Find your top usable ISO with an ISO ladder on a tripod | Greengo (project 3), Hudson night course | ISO 100–51200 | Yes: self-calibration (backlog) |
| Exposure | Scene wider than the sensor's range: bracket, merge, mask instead of a grad filter | Mike Browne (block 6), Hudson ATS 418, course ch. 9 | Bracket menu | Partly: EV and shutter steps over USB |
| Exposure | Pick the mode for the job: A for depth of field, S for motion, M for consistency | Greengo | Mode dial | No: camera-only |
| Focus | Decide where it must be sharp and how much depth of field; pick the AF mode for it | Mike Browne (block 5) | AFS, AFC, MF, AF area | Yes: focus mode, touch AF |
| Focus | Hyperfocal focusing with manual lenses at f/8–f/11 | Hudson ATS 430 | Manual M-mount lenses on the adapter | Yes: hyperfocal calculator (planned) |
| Focus | Focus stacking for near-to-far landscapes | Hudson ATS 408, night course; LUMIX PRO ch. 7 | Manual sequence today | Yes: focus bracketing via MF drive (backlog) |
| Sharpness | Slowest safe handheld shutter, with and without stabilization | Greengo (project 2); LUMIX PRO | IBIS settings | Partly: log and score the test |
| Sharpness | Each lens's sharpest aperture via an aperture sweep | Greengo (project 5) | — | Yes: self-calibration (backlog) |
| Light | Is the light right for this subject? If not, move, wait or reframe | Mike Browne (block 2), Hudson ch. 1, Greengo | — | No: judgement; in the /m checklist |
| Light | Choose white balance on purpose, set it at capture | Mike Browne (block 7) | WB, Kelvin, Auto Cool/Warm | Yes: /m and recipes |
| Light | Use a polarizer, and know when to skip it | Hudson ATS 425 | Kase CPL | No: checklist reminder |
| Look | In-camera looks with Real Time LUT and My Photo Style; grain and colour noise ride in the LUT | LUMIX PRO ch. 4; firmware 2.0 | 39 user LUTs loaded | Yes: batch load done; Looks and live trial next |
| Composition | Check the frame's edges; pre-visualize before raising the camera | Mike Browne (block 3), Greengo, Hudson ch. 2 | Grid and level | Partly: level is in the live view data |
| Lens | Focal length changes how subject and background relate | Mike Browne (block 4), Greengo (project 4) | — | No |
| Panorama | Rotate around the lens's no-parallax point | Hudson NPP video, course ch. 10 | Measure per lens | Partly: store it per lens |
| Motion | Shutter speed sets motion: frozen, blurred, trails, silky water | Greengo (project 1), Chase Jarvis | Shutter types (LUMIX PRO ch. 4) | Yes: shutter over USB |
| Time | Time-lapse and stop motion | Hudson ch. 11, LUMIX PRO ch. 4 | Built-in time-lapse | Yes: exposure ramping (backlog) |
| Night | Blue-hour foreground, star stacking, long-exposure NR, lens warmer | Hudson night course | — | Partly: interval and star-focus assist (backlog) |
| Setup | Make the screen show the final picture: constant preview on, live view boost off | LUMIX PRO ch. 4 | In `profiles/s9/s9_spec.yaml` | No: menu-only; the spec tracks it |
| Setup | One custom mode per kind of shoot | LUMIX PRO ch. 5 | C1 = Subtle Contrast 1, C3 = Cine Film 1 (to save) | No; phone recipes stand in |
| Video | 180° shutter, frame markers, exposure tools, skin-tone IRE | LUMIX PRO ch. 2–3 | Video mode | Partly |
| Habit | Pre-shot pass: you, light, composition, focal length, sharpness, exposure, white balance | Mike Browne, 7 Building Blocks | — | Yes: the /m checklist |

Video lessons are being transcribed locally; specifics from them will be added here as notes, not transcripts.
