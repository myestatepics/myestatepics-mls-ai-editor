#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
APP_NAME="MyEstatePics AI Editor - V8.0"
PYTHON="${PYTHON:-.venv/bin/python}"
rm -rf "build/$APP_NAME" "dist/$APP_NAME.app" "dist/$APP_NAME.dmg"
"$PYTHON" -m PyInstaller --noconfirm --clean --windowed --onedir \
  --name "$APP_NAME" --osx-bundle-identifier "com.myestatepics.aieditor.v8" \
  --runtime-hook packaging/v8_runtime.py \
  --add-data "prompts/mls_production.txt:prompts" \
  --add-data "prompts/v8_exterior.txt:prompts" \
  --add-data "prompts/v8_twilight.txt:prompts" v8_editor.py
PLIST="dist/$APP_NAME.app/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleDisplayName $APP_NAME" "$PLIST" || /usr/libexec/PlistBuddy -c "Add :CFBundleDisplayName string $APP_NAME" "$PLIST"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString 8.0" "$PLIST" || /usr/libexec/PlistBuddy -c "Add :CFBundleShortVersionString string 8.0" "$PLIST"
codesign --force --deep --sign - "dist/$APP_NAME.app"
codesign --verify --deep --strict "dist/$APP_NAME.app"
