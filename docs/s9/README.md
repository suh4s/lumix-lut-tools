# S9 control docs

What this fork adds on top of LUT management: driving a LUMIX S9 (firmware 2.1) from a Mac, a phone and Claude.

| Doc | What's in it |
| --- | --- |
| [ROADMAP.md](ROADMAP.md) | How the code reaches the camera, what works, the feature backlog (P1–P3), open tests |
| [FIRMWARE_FEATURES.md](FIRMWARE_FEATURES.md) | What firmware 2.0 and 2.1 opened, the My Photo Style probe, the Looks write-test plan |
| [PHONE_APP.md](PHONE_APP.md) | Phone paths (web app, Pi, native iPhone, Android), running `/m`, Tailscale, next steps |
| [TECHNIQUES.md](TECHNIQUES.md) | Techniques from courses I own, mapped to the S9 and to what code can drive |
| [SESSION_LOG.md](SESSION_LOG.md) | What was done, decisions, and the open checklist |

Related code and data on this branch:

| Path | What |
| --- | --- |
| `lumix/batch.py`, `control/lut_batch.py` | Batch-load a LUT pack from a manifest, with backup and verification |
| `control/lut_swap_test.py` | Does rewriting the active LUT slot change the look? |
| `control/mps_probe.py`, `docs/scans/mps_probe_fw2.1.json` | Read-only My Photo Style probe and its result on firmware 2.1 |
| `lumix/mobile.html`, `lumix/server.py` | Phone remote `/m`, token auth |
| `pipeline/vlog_preview.py` | Render a photo through every LUT in a folder (proof sheets) |
| `profiles/s9/` | My S9 setup as code (`s9_spec.yaml`, checker, tests) and the LUT pick manifest |
