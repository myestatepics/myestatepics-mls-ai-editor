# Building MyEstatePics AI Editor for macOS

The macOS release is a standalone Finder-launchable application. End users do
not need Python, a virtual environment, Terminal, or the Git repository.

## Prerequisites

- macOS on the same processor architecture as the target Macs
- Python 3.11 or newer for building
- Xcode Command Line Tools
- Project dependencies, including PyInstaller

Create the build environment once:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
```

## Build

Run:

```bash
./build_macos.sh
```

The script removes previous `build/`, `dist/`, and generated spec files, then
creates:

```text
dist/MyEstatePics AI Editor.app
```

It bundles the production prompt and any existing `config/`, `icons/`,
`resources/`, or `templates/` directories. PyInstaller's windowed mode prevents
a Terminal window from opening.

## Validate a release

Open the app from Finder, or use:

```bash
open "dist/MyEstatePics AI Editor.app"
```

Copy the `.app` to another directory and open that copy to confirm resource
portability. Validate Demo Mode without making an API call. Production Mode can
be checked for key recognition and readiness without starting a paid batch.

Unsigned local builds may require Control-click → Open on first launch.
Distribution to other Macs should use an Apple Developer ID signature and
notarization.

## User data

The packaged application never writes into its bundle. Writable data is stored
under:

```text
~/Library/Application Support/MyEstatePics AI Editor/
```

This includes `.env`, `preferences.ini`, startup diagnostics, the default
runtime folders, logs, history, and future cache data. Photos may be located
anywhere accessible to macOS, including cloud-synced folders, external drives,
USB storage, and mounted network volumes.

Startup diagnostics are written to:

```text
~/Library/Application Support/MyEstatePics AI Editor/Logs/application.log
```

## API key

For the packaged application create:

```text
~/Library/Application Support/MyEstatePics AI Editor/.env
```

with:

```text
OPENAI_API_KEY=sk-your-key
```

The **Open .env** button opens this location. **Reload API Key** applies changes
without restarting.

## Application icon

The build automatically uses the first `.icns` file found within three project
levels. If only a PNG is available, create an icon set containing these files:

```text
icon_16x16.png
icon_16x16@2x.png
icon_32x32.png
icon_32x32@2x.png
icon_128x128.png
icon_128x128@2x.png
icon_256x256.png
icon_256x256@2x.png
icon_512x512.png
icon_512x512@2x.png
```

Place them in `icons/MyEstatePics.iconset`, then run:

```bash
iconutil -c icns icons/MyEstatePics.iconset \
  -o icons/MyEstatePics.icns
```

Run `./build_macos.sh` again to configure Finder, Dock, and application icons.

## Clean and future releases

Every `./build_macos.sh` invocation is a clean build. For a future release,
update the single application version constant and the two bundle-version
values in the script, run tests, build, launch, relocate, and smoke-test the
result before signing and notarizing.

## Optional DMG

After building the app, run:

```bash
./build_dmg.sh
```

This creates `dist/MyEstatePics AI Editor.dmg` with the app and an Applications
shortcut. Signing and notarization should be added before public distribution.
