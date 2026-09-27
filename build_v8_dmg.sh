#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
APP_NAME="MyEstatePics AI Editor - V8.0"
STAGING="build/dmg-v8"
rm -rf "$STAGING"; mkdir -p "$STAGING"
ditto "dist/$APP_NAME.app" "$STAGING/$APP_NAME.app"
ln -s /Applications "$STAGING/Applications"
hdiutil create -volname "$APP_NAME" -srcfolder "$STAGING" -ov -format UDZO "dist/$APP_NAME.dmg"
