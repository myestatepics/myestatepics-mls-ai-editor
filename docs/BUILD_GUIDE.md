# Build Guide

## Development environment

- macOS
- Python 3.11 or newer
- Xcode Command Line Tools
- dependencies from `requirements.txt`

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
```

Source mode reads `.env` from the repository root.

## Run locally

```bash
.venv/bin/python myestatepics_ai_editor.py
```

The path is derived from `__file__`, so source launch does not depend on the
current working directory.

## Validate source

```bash
python3 -m py_compile myestatepics_ai_editor.py
pytest -q
```

Tests mock API responses and must not make paid calls.

## Build the app

```bash
./build_macos.sh
```

The script:

1. cleans `build/`, `dist/`, and generated spec files
2. bundles the prompt and optional resource directories
3. creates a windowed onedir app
4. sets bundle name, identifier, and v2.1 RC1 metadata
5. fails if a `.env` is bundled
6. applies and verifies an ad-hoc signature

Output:

`dist/MyEstatePics AI Editor.app`

## Create the DMG

```bash
./build_dmg.sh
```

Output:

`dist/MyEstatePics AI Editor.dmg`

The DMG contains the app and an Applications shortcut.

## Application icon

The build uses the first `.icns` found within three project levels. If none
exists, PyInstaller's standard macOS icon is used. See
[the detailed icon procedure](../BUILD_MAC.md#application-icon).

## Release checklist

- clean Git working tree
- application and prompt versions confirmed
- production prompt unchanged unless explicitly approved
- py_compile and pytest pass
- app and DMG rebuilt from clean outputs
- bundle signature and metadata verified
- no `.env`, real key, or repository path in app/DMG
- Finder, relocated app, and DMG-installed app launched
- remembered folder selects all eligible images
- Demo exercised without API calls
- release notes, changelog, commit, and tag prepared

See [Release Process](RELEASE_PROCESS.md) for the full workflow.
