# ADR-002: Portable macOS Packaging

**Status:** Accepted

## Problem

Operational users need Finder launch without Python, Terminal, a virtual
environment, or the Git repository.

## Decision

Build a windowed, onedir PyInstaller application with bundled read-only
resources, macOS bundle metadata, an ad-hoc internal signature, and an optional
DMG containing an Applications shortcut.

## Alternatives

- Require source launch: rejected for operational deployment.
- Use py2app: not selected because the current scripts and validation are built
  around PyInstaller.
- App Store packaging: deferred; current signing and distribution are internal.

## Consequences

Builds are architecture-specific unless a universal workflow is added. Public
distribution still requires Developer ID signing and notarization. Every
release must validate the actual app and DMG, not only Python tests.
