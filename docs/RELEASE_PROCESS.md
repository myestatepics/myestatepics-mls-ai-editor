# Release Process

## 1. Development

- Work from a focused branch or clean main checkout.
- Keep prompt, engine, GUI, packaging, and documentation changes separately
  scoped.
- Record architectural decisions under `docs/adr/`.
- Never commit `.env`, runtime output, customer photos, or API keys.

## 2. Testing

Run:

```bash
python3 -m py_compile myestatepics_ai_editor.py
pytest -q
```

Tests must use mocks or Demo Mode. Review failures before packaging.

## 3. Regression testing

Use representative interiors covering dark rooms, mixed lighting, warm wood,
white kitchens, windows, mirrors, bright walls, and ambiguous reflections.
Compare filenames, dimensions, EXIF, routing, verifier reasons, and visual
quality against [Quality Standards](QUALITY_STANDARDS.md).

## 4. Packaging

Run:

```bash
./build_macos.sh
./build_dmg.sh
```

Do not reuse old `dist/` output. Confirm metadata, resources, signature, and
secret exclusion.

## 5. Packaged validation

- launch the app through Finder
- check version and prompt badges
- confirm packaged key path is Application Support
- validate restored-folder automatic selection and cost
- manually uncheck, recheck, and clear selection
- verify Demo without a key or API client
- copy the app outside the repository and repeat launch
- mount the DMG, copy its app to a temporary folder, and launch it

Do not start a production batch merely to validate packaging.

## 6. Commit and tag

Commit only intended files. Use an annotated tag after release approval, for
example:

```bash
git tag -a v2.1.0-rc1 -m "Production v2.1 RC1"
```

Tag naming is a process recommendation; no tag is currently required by the
application.

## 7. GitHub release

Push the reviewed commit and tag, then create a prerelease containing:

- DMG
- checksum
- release notes and known limitations
- installation and API-key location
- macOS architecture and signing/notarization status

Do not upload `.env`, runtime folders, logs, photos, or raw history databases.

## 8. Post-release

Archive validation evidence, monitor reported failures, update
[CHANGELOG.md](CHANGELOG.md), and route new work through the roadmap or an ADR.
