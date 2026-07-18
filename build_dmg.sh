#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")"

APP_NAME="MyEstatePics AI Editor"
APP_PATH="dist/$APP_NAME.app"
DMG_PATH="dist/$APP_NAME.dmg"
STAGING_DIR="build/dmg"

if [[ ! -d "$APP_PATH" ]]; then
    echo "$APP_PATH does not exist. Run ./build_macos.sh first."
    exit 1
fi

rm -rf "$STAGING_DIR"
mkdir -p "$STAGING_DIR"
ditto "$APP_PATH" "$STAGING_DIR/$APP_NAME.app"
ln -s /Applications "$STAGING_DIR/Applications"
rm -f "$DMG_PATH"

hdiutil create \
    -volname "$APP_NAME" \
    -srcfolder "$STAGING_DIR" \
    -ov \
    -format UDZO \
    "$DMG_PATH"

echo "DMG created: $(pwd)/$DMG_PATH"
