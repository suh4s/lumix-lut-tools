#!/bin/zsh
# build_app.sh: build LumixOS.app (release) next to this script. Needs the repo .venv (camera service) to run.
#
# Plain build (ad-hoc signed, no virtual camera):   ./build_app.sh
# With the "LumixOS Camera" extension (needs a paid Apple Developer team; macOS refuses unsigned camera extensions):
#   TEAM_ID=ABCDE12345 SIGN_ID="Developer ID Application: ..." BUNDLE_ID=com.example.lumixos \
#   APP_PROFILE=app.provisionprofile EXT_PROFILE=camera.provisionprofile ./build_app.sh
# Profiles: App ID $BUNDLE_ID with System Extension capability + app group $TEAM_ID.$BUNDLE_ID.camera;
#           App ID $BUNDLE_ID.camera with the same app group. Developer ID builds must also be notarized.
set -e
cd ${0:a:h}
BUNDLE_ID=${BUNDLE_ID:-local.lumixos.app}
REPO=${PWD:h:h}   # baked into Info.plist so the app finds this repo's .venv without LUMIX_REPO
EXT_ID=$BUNDLE_ID.camera
swift build -c release
APP=LumixOS.app
rm -rf $APP && mkdir -p $APP/Contents/MacOS
cp .build/release/LumixOS $APP/Contents/MacOS/LumixOS
cat > $APP/Contents/Info.plist <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleName</key><string>LumixOS</string>
<key>CFBundleIdentifier</key><string>$BUNDLE_ID</string>
<key>CFBundleExecutable</key><string>LumixOS</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleShortVersionString</key><string>0.2</string>
<key>CFBundleVersion</key><string>2</string>
<key>LSMinimumSystemVersion</key><string>14.0</string>
<key>NSHighResolutionCapable</key><true/>
<key>LumixRepo</key><string>$REPO</string>
<key>NSCameraUsageDescription</key><string>LumixOS sends the LUMIX live view into OBS's virtual camera.</string>
</dict></plist>
PLIST

if [[ -z "$TEAM_ID" || -z "$SIGN_ID" ]]; then
  codesign --force -s - $APP
  echo "built $PWD/$APP (ad-hoc, without camera extension: set TEAM_ID and SIGN_ID to include it)"
  exit 0
fi

# --- camera extension ---
SYSX=$APP/Contents/Library/SystemExtensions/$EXT_ID.systemextension
mkdir -p $SYSX/Contents/MacOS
cp .build/release/LumixCamera $SYSX/Contents/MacOS/$EXT_ID
cat > $SYSX/Contents/Info.plist <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleName</key><string>LumixOS Camera</string>
<key>CFBundleDisplayName</key><string>LumixOS Camera</string>
<key>CFBundleIdentifier</key><string>$EXT_ID</string>
<key>CFBundleExecutable</key><string>$EXT_ID</string>
<key>CFBundlePackageType</key><string>SYSX</string>
<key>CFBundleShortVersionString</key><string>0.2</string>
<key>CFBundleVersion</key><string>2</string>
<key>LSMinimumSystemVersion</key><string>14.0</string>
<key>NSSystemExtensionUsageDescription</key><string>Shows the LUMIX live view from LumixOS as a camera in other apps.</string>
<key>CMIOExtension</key><dict><key>CMIOExtensionMachServiceName</key><string>$TEAM_ID.$EXT_ID</string></dict>
</dict></plist>
PLIST
TMP=$(mktemp -d)
cat > $TMP/ext.entitlements <<ENT
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>com.apple.security.app-sandbox</key><true/>
<key>com.apple.security.network.client</key><true/>
<key>com.apple.security.application-groups</key><array><string>$TEAM_ID.$EXT_ID</string></array>
</dict></plist>
ENT
cat > $TMP/app.entitlements <<ENT
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>com.apple.developer.system-extension.install</key><true/>
<key>com.apple.security.application-groups</key><array><string>$TEAM_ID.$EXT_ID</string></array>
</dict></plist>
ENT
[[ -n "$EXT_PROFILE" ]] && cp "$EXT_PROFILE" $SYSX/Contents/embedded.provisionprofile
[[ -n "$APP_PROFILE" ]] && cp "$APP_PROFILE" $APP/Contents/embedded.provisionprofile
codesign --force --options runtime --timestamp -s "$SIGN_ID" --entitlements $TMP/ext.entitlements $SYSX
codesign --force --options runtime --timestamp -s "$SIGN_ID" --entitlements $TMP/app.entitlements $APP
codesign --verify --deep --strict $APP
echo "built $PWD/$APP with camera extension $EXT_ID (team $TEAM_ID)"
