# Phone control

Yes, a phone can do everything the Mac does. One path already works: the phone talks to `lumix-server` on the Mac,
and the Mac drives the camera over USB. A native iPhone app with no Mac in between is plausible but needs a short
spike first.

## Paths

| Path | How it reaches the S9 | Status | Effort | Trade-off |
| --- | --- | --- | --- | --- |
| Phone → Mac (`/m`) | Safari web app → `lumix-server` on the Mac → USB | Working, tested on the S9 | done | Needs the Mac and the cable |
| Phone → Raspberry Pi | Same server on a Pi clipped to the camera; USB control works on Linux | Ready to try | S | Portable; a Pi and a battery in the bag |
| Native iPhone, USB-C | ImageCaptureCore `requestSendPTPCommand` (iOS 13+) | Needs a spike | L | No Mac; Apple's sandbox decides what passes |
| Native, Wi-Fi + Bluetooth | Pair over Bluetooth, commands over Wi-Fi, as LUMIX Lab does | Needs reverse-engineering | L+ | Cable-free; the most work |
| Android, USB | USB host mode passes raw PTP (the protocol was decoded from LUMIX Lab for Android) | Possible | M | Only with an Android phone |

Evidence for the native USB path: Apple documents
[`requestSendPTPCommand`](https://developer.apple.com/documentation/imagecapturecore/iccameradevice/requestsendptpcommand(_:outdata:completion:)),
developers report errors with it on iOS ([forum](https://developer.apple.com/forums/thread/656878)), and LUMIX Lab
3.0 controls the S9 over a USB cable from a phone
([Panasonic](https://www.panasonic.com/global/consumer/lumix/lumix-lab-app.html)).

## `/m` today

`lumix/mobile.html` is a single-file web app served by `lumix-server`.

- Live view (MJPEG), tap to focus, shutter, AF, undo.
- Manual-focus nudges (near fast/slow, far slow/fast).
- Every setting the camera offers, built from `/state` options, including Real Time LUT strength and colour noise.
- Recipes: one tap applies Photo Style, WB, Kelvin, G/M shift, ISO, EV, aperture, shutter, format, focus mode, LUT
  on/strength and colour noise. Stored in the phone's `localStorage` (`lumix.recipes`).
- Live histogram from the frames, and a pre-shot checklist (Mike Browne's seven building blocks plus S9 checks:
  native ISO for the style, the LUT you meant).
- Add to Home Screen via `/manifest.webmanifest`.

## Running it

```sh
# quit LumixOS first: only one program can hold the USB link
.venv/bin/python -m lumix.server --host 0.0.0.0 --port 8767
```

- On a non-loopback host the server generates a token and prints a `/m?t=…` address. Open it on the phone, then
  Share → Add to Home Screen. The token is kept in a cookie after the first visit.
- Requests need the token as an `X-Lumix-Token` header, a `?t=` parameter, or the `lumix_t` cookie; anything else
  gets 401. Set your own with `--token` or `LUMIX_TOKEN`.
- macOS asks once whether Python may accept incoming connections.
- Port 8765 is often taken (AnkiConnect uses it); LumixOS reads `LumixPort` from its defaults.

## Away from home Wi-Fi

`/m` works over Tailscale as long as **both** the Mac and the phone are on the tailnet: open
`http://<mac's tailnet name>:8767/m?t=…`. If the page doesn't load, check the phone shows "Connected" in the
Tailscale app first; the server and token work the same either way.

## Next

1. Use `/m` for a week and note what is missing.
2. Add: Looks picker (My Photo Style 1–4 now, 1–10 once shown), live LUT trial, base-ISO guard with an ND suggestion,
   hyperfocal calculator for manual lenses, night mode with a red UI and interval timer, shot journal.
3. iPhone spike: a one-screen Swift app that finds the S9 with ImageCaptureCore and sends GetLutInfo (0x9414). If
   the LUT list comes back, the native path is open.
4. Camera broker, so LumixOS, the Claude plug-in and the phone can share the camera.
