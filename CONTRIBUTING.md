# Contributing

## Local setup

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e .[dev]
```

## Running tests

```bash
pytest                       # unit tests
COMFYBULK_REAL_TEST_CLIP=/path/to/clip.mp4 pytest -m integration
```

Integration tests are opt-in and require a real media file plus ffmpeg/ffprobe.
When using Windows ffmpeg.exe from WSL, set COMFYBULK_INTEGRATION_TMP to a
Windows-visible scratch directory if the repo checkout is not visible to ffmpeg.

## Style

- Small, dense modules — no unnecessary abstraction layers.
- Type hints only where they aid readability.
- Comments only when *why* is non-obvious.
- All ffmpeg paths run through `comfybulk.ffmpeg.to_win()` before subprocess.

## PRs

One PR per logical change. Include a short description and, for behavior changes, a manual-test note (which command you ran, what you observed).

## Issues vs Discussions

- **Issues**: bug reports, feature requests with concrete acceptance criteria.
- **Discussions**: open-ended questions, design proposals, "should we…" threads.
