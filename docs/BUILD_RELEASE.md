# Direct macOS Build and Release

## Purpose and provenance

This procedure builds the side-by-side Apple Silicon application:

```text
MyEstatePics AI Editor - Direct V4.0.app
```

Build only from a clean checkout of the approved production commit or its
documentation-only freeze descendant. Record:

```bash
git branch --show-current
git rev-parse HEAD
git status --short
```

Never package `.env`, customer photographs, runtime data, logs, databases,
`release/`, or `test-output/`.

## Build environment

Requirements:

- Apple Silicon macOS;
- Python 3.11 or newer matching the target architecture;
- Xcode Command Line Tools;
- dependencies from `requirements.txt`; and
- enough free space for PyInstaller and DMG staging.

Create the environment:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
```

Validate versions:

```bash
.venv/bin/python --version
.venv/bin/python -m PyInstaller --version
```

## Offline pre-build validation

```bash
.venv/bin/python -m py_compile myestatepics_ai_editor.py
.venv/bin/python -m pytest -q
git diff --check
```

Tests must use mocks or Demo Mode. Do not start a paid production batch during
build validation.

## Clean rebuild

Remove generated build products only:

```bash
rm -rf build dist
```

Do not delete `release/`, `test-output/`, source files, or Application Support
data as part of a clean rebuild.

## PyInstaller command

Run from the repository root:

```bash
.venv/bin/python -m PyInstaller \
  --noconfirm \
  --clean \
  --windowed \
  --onedir \
  --name "MyEstatePics AI Editor - Direct" \
  --osx-bundle-identifier "com.myestatepics.aieditor.direct" \
  --runtime-hook packaging/direct_runtime.py \
  --add-data "prompts/mls_production.txt:prompts" \
  myestatepics_ai_editor.py
```

The runtime hook sets the Direct identity before application import. This
isolates `.env`, settings, logs, database/history, and default runtime folders
under:

```text
~/Library/Application Support/MyEstatePics AI Editor - Direct/
```

If an approved `.icns` source asset is available, add:

```text
--icon path/to/MyEstatePics.icns
```

The existing internal installer uses the bundled macOS icon. Do not substitute
an unapproved icon during a production rebuild.

## Bundle metadata

Set and verify the generated Info.plist:

```bash
PLIST="dist/MyEstatePics AI Editor - Direct V4.0.app/Contents/Info.plist"
/usr/libexec/PlistBuddy -c \
  "Set :CFBundleDisplayName MyEstatePics AI Editor - Direct V4.0" "$PLIST"
/usr/libexec/PlistBuddy -c \
  "Set :CFBundleName MyEstatePics AI Editor - Direct V4.0" "$PLIST"
/usr/libexec/PlistBuddy -c \
  "Set :CFBundleIdentifier com.myestatepics.aieditor.direct" "$PLIST"
/usr/libexec/PlistBuddy -c \
  "Set :CFBundleShortVersionString 4.0" "$PLIST"
/usr/libexec/PlistBuddy -c \
  "Set :CFBundleVersion 4.0" "$PLIST"
plutil -lint "$PLIST"
plutil -p "$PLIST"
```

Current metadata:

| Field | Value |
| --- | --- |
| Display/name | `MyEstatePics AI Editor - Direct V4.0` |
| Bundle identifier | `com.myestatepics.aieditor.direct` |
| Short version | `4.0` |
| Bundle version | `4.0` |
| Architecture | Apple Silicon |

The bundle version mirrors the existing production installer metadata. This
documentation freeze does not change application versioning.

## Secret and resource checks

```bash
test -f \
  "dist/MyEstatePics AI Editor - Direct V4.0.app/Contents/Frameworks/prompts/mls_production.txt"
cmp prompts/mls_production.txt \
  "dist/MyEstatePics AI Editor - Direct V4.0.app/Contents/Frameworks/prompts/mls_production.txt"
! find "dist/MyEstatePics AI Editor - Direct V4.0.app" -type f -name '.env' -print -quit \
  | grep -q .
```

The prompt comparison must produce no output and exit successfully. The secret
check must find no `.env`.

## Signing

The current internal build is ad-hoc signed:

```bash
codesign --force --deep --sign - \
  "dist/MyEstatePics AI Editor - Direct V4.0.app"
codesign --verify --deep --strict --verbose=2 \
  "dist/MyEstatePics AI Editor - Direct V4.0.app"
codesign -dv --verbose=4 \
  "dist/MyEstatePics AI Editor - Direct V4.0.app"
```

If a valid Apple Developer ID Application certificate is available and public
distribution is approved, replace `-` with the exact signing identity:

```bash
codesign --force --deep --options runtime --timestamp \
  --sign "Developer ID Application: LEGAL NAME (TEAMID)" \
  "dist/MyEstatePics AI Editor - Direct V4.0.app"
```

Developer ID signing does not itself notarize the DMG. Never describe an
ad-hoc-signed artifact as Developer ID signed or notarized.

## DMG creation

```bash
rm -rf build/dmg
mkdir -p build/dmg
ditto "dist/MyEstatePics AI Editor - Direct V4.0.app" \
  "build/dmg/MyEstatePics AI Editor - Direct V4.0.app"
ln -s /Applications build/dmg/Applications
rm -f "dist/MyEstatePics AI Editor - Direct V4.0.dmg"
hdiutil create \
  -volname "MyEstatePics AI Editor - Direct V4.0" \
  -srcfolder build/dmg \
  -ov \
  -format UDZO \
  "dist/MyEstatePics AI Editor - Direct V4.0.dmg"
```

## Offline packaged smoke test

Without starting production processing:

1. launch the `.app`;
2. confirm the main window, application badge, and prompt badge;
3. confirm the prompt loads;
4. choose folders;
5. check Low and Medium selection and estimates;
6. exercise Demo Mode and Cancel;
7. confirm no startup API call occurs; and
8. quit normally.

Launch command:

```bash
open "dist/MyEstatePics AI Editor - Direct V4.0.app"
```

## Verification and checksums

```bash
file \
  "dist/MyEstatePics AI Editor - Direct V4.0.app/Contents/MacOS/MyEstatePics AI Editor - Direct V4.0"
codesign --verify --deep --strict \
  "dist/MyEstatePics AI Editor - Direct V4.0.app"
hdiutil verify "dist/MyEstatePics AI Editor - Direct V4.0.dmg"
shasum -a 256 "dist/MyEstatePics AI Editor - Direct V4.0.dmg"
du -sh \
  "dist/MyEstatePics AI Editor - Direct V4.0.app" \
  "dist/MyEstatePics AI Editor - Direct V4.0.dmg"
```

Mount the DMG and verify its app has the same prompt checksum, bundle
identifier, executable architecture, and valid signature as the staged app.

## Release folder structure

Generated artifacts remain untracked. Stage approved deliverables as:

```text
release/<commit-short>-direct/
├── MyEstatePics AI Editor - Direct V4.0.app/
└── MyEstatePics AI Editor - Direct V4.0.dmg
```

Record the full source commit, DMG SHA-256, app and DMG sizes, architecture,
signature type, notarization status, smoke-test result, and confirmation that
no API call was made. Do not commit `release/`.
