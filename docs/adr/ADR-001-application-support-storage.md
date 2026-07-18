# ADR-001: Application Support Storage

**Status:** Accepted

## Problem

A portable `.app` bundle is read-only in practice and may move between folders.
Preferences, secrets, logs, runtime output, and history cannot safely depend on
the repository or bundle location.

## Decision

Packaged writable state uses:

`~/Library/Application Support/MyEstatePics AI Editor/`

This includes `.env`, `preferences.ini`, startup logs, default runtime folders,
data, and cache. Source mode keeps repository-local runtime results and reads
the repository `.env`; QSettings preferences and startup logs still use
Application Support.

## Alternatives

- Store beside the executable: rejected because it breaks portability and may
  be unwritable.
- Use the repository for all modes: rejected because end users do not have it.
- Store the API key in source or bundle: rejected as insecure.

## Consequences

The app is relocatable and user state survives replacement. Backup and support
procedures must account for Application Support. Uninstalling the app does not
automatically delete user data.
