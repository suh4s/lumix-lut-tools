# S9 control: what was done, decided and left open

Work log for the `s9-control` branch, 2026-11-17 → 2026-11-18. Camera: DC-S9, firmware 2.1.

## Done

| Area | What | Where |
| --- | --- | --- |
| Firmware | S9 updated 1.9 → 2.1; camera settings backed up first; lens firmware checked | camera |
| LUTs | 41 V-Log LUTs from two packs renamed; proof sheet rendered on a V-Log test photo; 22 picks | `pipeline/vlog_preview.py`, `profiles/s9/s9-picks.csv` |
| LUTs | 21 picks batch-loaded into slots 20–40 with V-Log base, each verified by read-back (Cine Film 2 held back: library full) | `lumix/batch.py`, `control/lut_batch.py` |
| Safety | `push` refuses an occupied slot without `--replace`; the library is backed up before a batch load | `control/lumix_lut.py`, `lumix/batch.py` |
| Fixes | `lut_batch list` no longer crashes on unknown Photo Style codes; V-Log uploads read back as style 40 are accepted | `control/lut_batch.py`, `lumix/batch.py` |
| Claude plug-in | `lumix-mcp` registered in Claude Desktop (17 tools; read all 41 LUTs in a test) | README |
| LumixOS | Built and run: live view at 30 fps; port and repo path made configurable | `apps/LumixOS` |
| Phone | `/m` web app with live view, touch AF, shutter, MF, all settings, recipes, histogram, checklist; token auth for non-loopback hosts. Works on home Wi-Fi | `lumix/mobile.html`, `lumix/server.py` |
| Test | LUT-swap test written (red test LUT into the active slot, restore verified to 1e-5) | `control/lut_swap_test.py` |
| Firmware 2.0 | My Photo Style probed read-only: codes 19–28, names, shown/hidden, slots 1–4 selectable over USB | `control/mps_probe.py`, `docs/scans/mps_probe_fw2.1.json` |
| Settings | `PHOTO_STYLES` corrected: 19–28 are My Photo Style 1–10 (not custom1–3 at 20–22) | `lumix/settings.py` |
| Research | Courses inventoried; techniques mapped to the S9 and to code | [TECHNIQUES.md](TECHNIQUES.md) |
| Docs | Roadmap, firmware feature map, phone paths | [ROADMAP.md](ROADMAP.md), [FIRMWARE_FEATURES.md](FIRMWARE_FEATURES.md), [PHONE_APP.md](PHONE_APP.md) |

## Decisions

- **USB, not Wi-Fi**, for control: the decoded protocol is complete enough, and LUMIX Lab mode (04da:2387) carries
  both LUT management and settings.
- **V-Log base** for all pack LUTs (tag-less `.cube`, base 65535): they were made for V-Log.
- **Firmware commands are never sent** (0x9606/0x9607 refused in `ptp.py`).
- **Read-only first** for anything new on the camera (probe before write; record and restore every value).
- **Phone first as a web app** served by the Mac; native iPhone only after a spike proves ImageCaptureCore passes
  vendor commands.
- **Public repo hygiene**: no purchase details, cloud-drive paths, network addresses, tokens or course transcripts.

## Open

- [ ] LUT-swap test: select Vibrant Pop (slot 40) on the camera, then `lut_swap_test.py OUT --slot=40 --yes`.
- [ ] My Photo Style write test: rerun `control/mps_write_test.py OUT --yes` with the lens cap off, pointed at a lit,
  colourful scene (run 1 was inconclusive; camera restored).
- [ ] Restart Claude Desktop so `lumix-mcp` loads; check it lists LUTs.
- [ ] Join the iPhone to Tailscale to use `/m` away from home.
- [ ] On the camera: install the waiting lens firmware; save C1 = Subtle Contrast 1 and C3 = Cine Film 1, then back
  up settings again.
- [ ] Swap Cine Film 2 in for a pick you use least.
- [ ] Map Photo Style codes 14, 16, 40–45, 48, 50 to names.
- [ ] Fold specifics from the course videos into TECHNIQUES.md as notes.
