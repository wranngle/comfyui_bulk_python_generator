# Security Policy

## Reporting a vulnerability

Email security reports privately. Please do not open public issues for security-sensitive findings.

Include: description, reproduction steps, affected version/commit, and any suggested mitigation.

## Scope

This project shells out to local `ffmpeg.exe` and a local Ollama instance. It does not accept network input. Security-relevant issues are mostly limited to:

- Command injection through unsanitized paths/captions reaching ffmpeg filter strings.
- Path traversal when extracting metadata from media files.
- Unsafe deserialization of ComfyUI workflow JSON.

## Supported versions

Only the latest `main` branch is supported.
