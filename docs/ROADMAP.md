# Roadmap

For the S9 control work (broker, Looks via My Photo Style, phone app, feature backlog) see
[s9/ROADMAP.md](s9/ROADMAP.md).

1. Verify the LevelInfo units/sign (tilt the camera) and the mode codes other than A (0x02000080).
2. Hardware JPEG decode (VideoToolbox) in LumixOS; focus point needs Wi-Fi (see 5).
3. Photo Style fine-tune props (0x02000012–0x0200001C), LUT 2 (0x020001F8/FA/FC).
4. Gallery download over PTP (0x9911–0x9917) so captures stay on the card.
5. Wi-Fi (cam.cgi / LUMIX Lab's Wi-Fi stream has focus frames).

Known limits: active LUT selection, P/A/S/M, custom modes, i.Dynamic and USB focus-frame position are not available.
