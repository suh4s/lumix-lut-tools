# LumixOS

Native macOS live view and control for a LUMIX camera on USB (PC (Tether) mode). See the main README for features.

Build: `./build_app.sh` → `LumixOS.app` (ad-hoc signed). Copy it to /Applications. It starts the camera service from
the `.venv` of the repo it was built in (override: `LUMIX_REPO`, or `defaults write local.lumixos.app LumixRepo <path>`);
log: `~/Library/Logs/LumixOS/server.log`.

Port: 127.0.0.1:8765 by default. Anki's AnkiConnect add-on uses the same port; pick another with
`defaults write local.lumixos.app LumixPort -int 8767` (or `LUMIX_PORT`), then reopen the app. The OBS Media Source URL
and Camera → Copy Stream URL follow the setting; the LumixCamera extension still expects 8765.

## Virtual camera without signing: OBS Virtual Camera

OBS (30+) ships a signed CoreMediaIO extension, "OBS Virtual Camera", with a sink stream that accepts frames from any
process. Camera → Send Live View to OBS Virtual Camera feeds it (1920x1080 BGRA, letterboxed); Zoom/Meet/FaceTime
then pick "OBS Virtual Camera". One-time setup: install OBS, click Start Virtual Camera once, allow the extension in
System Settings, stop it in OBS. OBS does not need to run afterwards. `Sources/LumixOS/OBSCameraFeed.swift`.

## Virtual camera ("LumixOS Camera")

`Sources/LumixCamera` is a CoreMediaIO camera extension: Zoom, Meet, FaceTime, OBS and QuickTime see a camera named
"LumixOS Camera" (1280x960 or 1920x1080, 30 fps) showing the live view while LumixOS runs, and a hint card otherwise.
Install/remove from the app's **Camera** menu.

macOS loads camera extensions only when they are signed by an Apple Developer team (restricted entitlement
`com.apple.developer.system-extension.install` + provisioning profiles; Developer ID builds must be notarized).
Without a team, `build_app.sh` leaves the extension out. With one:

1. developer.apple.com → Identifiers: App ID `<bundle id>` (capabilities: System Extension, App Groups) and
   `<bundle id>.camera` (App Groups); app group `<TEAM_ID>.<bundle id>.camera` on both.
2. Profiles for both App IDs (Developer ID, or Mac Development for local testing), download them.
3. `TEAM_ID=… SIGN_ID="Developer ID Application: …" BUNDLE_ID=<bundle id> APP_PROFILE=app.provisionprofile
   EXT_PROFILE=camera.provisionprofile ./build_app.sh`, then notarize (`xcrun notarytool submit … --wait`, `xcrun stapler staple`).
4. Copy to /Applications, Camera → Install LumixOS Camera, allow it in System Settings → General → Login Items & Extensions.

Without a team: OBS → Media Source → `http://127.0.0.1:8765/stream` (Camera → Copy Stream URL), then OBS's Virtual Camera.
