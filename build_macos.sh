#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")"

APP_NAME="MyEstatePics AI Editor - Direct V4.0.7"
BUNDLE_ID="com.myestatepics.aieditor.direct"
RELEASE_VERSION="4.0.7"
PYTHON="${PYTHON:-}"

if [[ -z "$PYTHON" ]]; then
    if [[ -x ".venv/bin/python" ]]; then
        PYTHON=".venv/bin/python"
    else
        PYTHON="python3"
    fi
fi

if ! "$PYTHON" -m PyInstaller --version >/dev/null 2>&1; then
    echo "PyInstaller is not installed for $PYTHON."
    echo "Install build dependencies with:"
    echo "  $PYTHON -m pip install -r requirements.txt"
    exit 1
fi

echo "Cleaning previous macOS builds..."
rm -rf build
mkdir -p dist
find dist -mindepth 1 -maxdepth 1 -exec rm -rf {} +
rm -f ./*.spec

PYINSTALLER_ARGS=(
    --noconfirm
    --clean
    --windowed
    --onedir
    --name "$APP_NAME"
    --osx-bundle-identifier "$BUNDLE_ID"
    --runtime-hook "packaging/direct_runtime.py"
    --add-data "prompts/mls_production.txt:prompts"
)

for resource_dir in config icons resources templates; do
    if [[ -d "$resource_dir" ]]; then
        PYINSTALLER_ARGS+=(--add-data "$resource_dir:$resource_dir")
    fi
done

ICON_PATH=""
while IFS= read -r candidate; do
    ICON_PATH="$candidate"
    break
done < <(find . -maxdepth 3 -type f -name '*.icns' -print)

if [[ -n "$ICON_PATH" ]]; then
    echo "Using application icon: $ICON_PATH"
    PYINSTALLER_ARGS+=(--icon "$ICON_PATH")
else
    echo "No .icns icon found; using the standard macOS application icon."
    echo "See BUILD_MAC.md for icon-generation instructions."
fi

echo "Building $APP_NAME.app..."
"$PYTHON" -m PyInstaller "${PYINSTALLER_ARGS[@]}" myestatepics_ai_editor.py

PLIST="dist/$APP_NAME.app/Contents/Info.plist"
if [[ ! -f "$PLIST" ]]; then
    echo "Build failed: $PLIST was not generated."
    exit 1
fi

if find "dist/$APP_NAME.app" -type f -name '.env' -print -quit | grep -q .; then
    echo "Build failed: a secret .env file was included in the app bundle."
    exit 1
fi

set_plist_string() {
    local key="$1"
    local value="$2"
    if ! /usr/libexec/PlistBuddy -c "Set :$key $value" "$PLIST" >/dev/null 2>&1; then
        /usr/libexec/PlistBuddy -c "Add :$key string $value" "$PLIST"
    fi
}

set_plist_string "CFBundleDisplayName" "$APP_NAME"
set_plist_string "CFBundleName" "$APP_NAME"
set_plist_string "CFBundleIdentifier" "$BUNDLE_ID"
set_plist_string "CFBundleShortVersionString" "$RELEASE_VERSION"
set_plist_string "CFBundleVersion" "$RELEASE_VERSION"

echo "Applying local ad-hoc signature after metadata updates..."
codesign --force --deep --sign - "dist/$APP_NAME.app"
codesign --verify --deep --strict "dist/$APP_NAME.app"

echo
echo "Build successful:"
echo "  $(pwd)/dist/$APP_NAME.app"
echo
echo "Launch with Finder or run:"
echo "  open \"dist/$APP_NAME.app\""
