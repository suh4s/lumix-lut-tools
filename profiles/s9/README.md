# s9-config — your LUMIX S9 setup, kept like code

| File | What it is |
|---|---|
| `s9_spec.yaml` | Source of truth: base settings + C1/C2/C3 (+ `night`), mapped from Hudson Henry's Nikon banks |
| `s9check.py` | **Read-only** checker: asks the camera what it's set to over Wi-Fi and compares with the spec |
| `test_s9check.py` | 20 unit tests (no camera needed): `python3 -m unittest -v test_s9check.py` |
| `camset/` | *(you create it)* the camera's own settings file, copied off the SD card and committed |

## Safety model
`s9check.py` can only send `getstate`, `getinfo`, `getsetting`, and the pairing request (`accctrl`). Any other
command raises `SafetyError` before the request is built, and a test enforces this. Pairing makes the S9 show an
approval prompt. That's the only thing that appears on the camera.

> The Wi-Fi protocol is reverse-engineered: it's from the open-source *lumix-remote* project, verified on the
> original S5 and **untested on the S9**. Expect to learn things on the first run.

## One-time setup
```bash
pip3 install pyyaml
python3 s9check.py plan --bank C2      # shows exactly which URLs it would call
```
On the camera: **MENU ▸ Setup ▸ Wi-Fi ▸ Wi-Fi Function ▸ New Connection ▸ Remote Shooting & View**.
The menu names come from the S5, so confirm them on the S9. Then join the camera's Wi-Fi network from your Mac.
The camera's address is `192.168.54.1`.

## The loop (spec → camera → snapshot → drift check)
1. **Set** the camera from `s9_spec.yaml`. Do the `base` settings first, then set each mode and use
   *Save to Custom Mode* for C1, C2 and C3.
2. **Back up**: Setup ▸ *Save/Restore Camera Setting* ▸ Save (e.g. `S9BASE01`). Copy the file into `camset/`.
3. **Identify**, with no pairing: `python3 s9check.py identify` checks the model and firmware (needs 2.1 or later).
4. **Probe** once: `python3 s9check.py probe --out probe/`. Send me `getinfo_allmenu.xml`. It lists every
   setting the S9 can report, so we can move more spec lines from MANUAL to automatic.
5. **Check** each bank with the dial on that bank:
   `python3 s9check.py check --bank C2 --save snapshots/C2.json`
6. **Catch drift** later: `python3 s9check.py check --bank C2 --golden snapshots/C2.json`.
   It exits with code 1 if anything changed.

## Reading the table
- **PASS / FAIL**: the spec has an `expect` value and the camera's value matches it or doesn't.
- **OBSERVED**: the camera's value was read and saved in the snapshot, with no rule attached yet.
- **UNREADABLE**: the camera rejected that read. Note it, then probe.
- **MANUAL**: there's no Wi-Fi read for this setting yet, so check it by eye using the `menu` path.

A first-run FAIL may only mean the S9 encodes a value differently (for example `afc` vs `AF-C`).
Fix the `expect` value in the spec, re-run, and commit.
