# Changelog

## 0.1.1

- Monitor global instructions and CLI rules, skills and plugins, including file additions, edits, renames and removals.
- Serve cached acceptance on status polls; keep fresh configuration checks before generation.
- Batch delta persistence at 75ms / 8KiB and throttle Markdown repaint to 100ms; preserve final and partial responses.
- Renew the browser lease from the current owner's HTTP stream, with keepalive traffic; keep heartbeat, disconnect grace and Job cleanup. Rotated owners and observer streams cannot renew the lease.
- Defer full-text search scaling and per-conversation Web access controls until their need and CLI behavior are established.

No real CLI diagnostics, model sends or independent agents were used for this update.

## 0.1.0

- Windows 11 x64 source/ZIP distribution with a guided local setup.
- Per-install CLI discovery and Python 3.14 environment setup.
- Explicit configuration and feature consent, separate from live-model acceptance.
- Dedicated quota diagnostic workspace and version-independent startup recognition.
- UTF-8 hook launchers work from Japanese installation paths in hidden processes.
- MIT license, third-party notices, reproducible archive content and checksums.

Real CLI diagnostics and model sends have not been performed for this release.
Existing integration evidence uses CLI 1.3.0; later versions are unverified.
