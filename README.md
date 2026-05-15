# comfyui_bulk_python_generator

Bulk post-processing pipeline for ComfyUI-generated video. Selects clips, builds layout variants (`grid`, `montage`, `single`, `cta_only`), runs ffmpeg effects (glitch, reverse, rainbow border, motion blur), mixes random audio, and can fill metadata via a local Ollama LLM.

Ported from a tangle of PowerShell scripts into a single Python package. The current implementation is opinionated around WSL/Python shelling out to Windows `ffmpeg.exe`/`ffprobe.exe`; do not assume generic Linux/macOS portability.

## Quickstart (60-second First User Moment)

No ComfyUI, no ffmpeg, no `config.toml` — just the bundled fixture clips at `tests/fixtures/samples/`. Verifies metadata extraction works end-to-end on a fresh clone.

```bash
git clone https://github.com/wranngle/comfyui_bulk_python_generator.git && cd comfyui_bulk_python_generator
python3 -m venv .venv && . .venv/bin/activate && pip install -e .
cp -r tests/fixtures/samples /tmp/comfybulk-quickstart && cd /tmp/comfybulk-quickstart
python3 -c "from comfybulk.extract import process_directory; ok, fail = process_directory('.', 'metadata.csv', test_mode=False); print(f'{ok} extracted, {fail} failed')"
head -n 4 metadata.csv
```

Expected: `3 extracted, 0 failed` and a 4-line `metadata.csv` (header + one row per fixture clip). From here, point the full pipeline at your own ComfyUI output directory — see [Run](#run).

## Layout

```
src/comfybulk/
├── config.py     # paths from config.toml + env
├── ffmpeg.py     # subprocess wrappers, WSL→Windows path conversion
├── extract.py    # ComfyUI metadata extraction (PNG/MP4 → metadata.csv)
├── fill.py       # Ollama LLM fills empty CSV fields
├── organize.py   # group media files by prompt into subfolders
├── effects.py    # glitch_negative, rainbow_overlay, reverse_ending
├── audio.py      # random audio mix with time-stretch + loudnorm
├── variants.py   # grid (2x2), montage, single, cta_only
├── tools.py      # fastpingpong, timestretch, convert (webp/webm → mp4)
├── pipeline.py   # bulk orchestrator (entry point of the pipeline)
├── checkpoint/   # resume-on-failure JSONL ledger
├── __main__.py   # CLI: `comfybulk <subcommand>`
└── data/         # bundled with the package via importlib.resources
    ├── ai_metadata_prompts.csv  # LLM prompt templates per metadata field
    └── captions.csv             # CTA caption strings

config.example.toml              # template — copy to config.toml + edit paths
```

## Install

```bash
# editable install for development (from a Linux-native path; building from /mnt/c
# may fail with NTFS permission errors on the egg-info dir)
git clone https://github.com/wranngle/comfyui_bulk_python_generator.git
cd comfyui_bulk_python_generator
python3 -m venv .venv && . .venv/bin/activate
pip install -e .[dev]
cp config.example.toml config.toml      # edit paths to match your setup
```

Or build a wheel and install it from anywhere:

```bash
pip wheel --no-deps --wheel-dir dist .
pip install dist/comfyui_bulk_python_generator-0.1.0-py3-none-any.whl
```

## Run

```bash
# full pipeline (one grid output)
comfybulk pipeline --source D:\\path\\to\\ComfyUI\\output\\favorites\\assemblymaker --variant grid

# multiple variants and quantities (grid + montage, 3 each)
comfybulk pipeline --source D:\\path\\to\\clips --variant grid --variant montage --quantity 3

# reproducible clip/CTA/audio selection + manifest file
comfybulk pipeline --source D:\\path\\to\\clips --variant montage --seed 1234 --write-manifest

# resume an interrupted batch (skips iterations already in .comfybulk-checkpoint.jsonl)
comfybulk pipeline --source D:\\path\\to\\clips --variant single --quantity 50 --resume

# clean variant only (no effects stack)
comfybulk pipeline --source D:\\path\\to\\clips --variant single --no-effects

# extract metadata only
comfybulk extract --path D:\\path\\to\\ComfyUI\\output\\favorites\\assemblymaker

# fill empty LLM fields
comfybulk fill

# organize media by prompt
comfybulk organize --path D:\\path\\to\\ComfyUI\\output\\favorites

# standalone effects
comfybulk glitch --input video.mp4 --output-dir out/
comfybulk rainbow --action apply --input video.mp4 --output out.mp4
comfybulk reverse --input video.mp4 --output-dir out/

# standalone tools
comfybulk pingpong --input video.mp4 --speed 1000
comfybulk timestretch --input video.mp4 --stretch 2.0
comfybulk convert --input image.webp --output video.mp4
comfybulk mixaudio --input video.mp4
```

## Variant types

`grid` (2x2 ping-pong layout) · `montage` (2-3 sequential clips) · `single` (one clip 1080x1920) · `cta_only` (CTA folder pick with caption).

The bulk pipeline applies the full effects stack (glitch -> reverse -> rainbow -> motion blur) by default. With `--no-effects`, `single`, `montage`, and `cta_only` use the clean builders and may produce CTA/no-CTA outputs. `grid --no-effects` produces a raw grid output.

## Behavior notes

- `pipeline` does not move your source library by default. Pass `--organize` only when you explicitly want a best-effort `organize(cfg.paths.favorites_root)` pre-step.
- `comfybulk organize --path ...` moves root-level MP4 files into prompt-named folders based on ffprobe `comment` metadata. PNG companions are matched by seed, model family, or base filename pattern. There is intentionally no filename-only prompt fallback.
- ComfyUI generation seeds are extracted, validated, and written to metadata for matching/renaming. They are separate from the pipeline RNG seed.
- Clip selection, montage size, CTA/caption/audio choices, and other pipeline random choices can be seeded with `--seed`.
- `--write-manifest` appends run metadata to `finals/pipeline_manifest.jsonl`; without it, manifest details are printed only.
- `--resume` reads `<source>/.comfybulk-checkpoint.jsonl` and skips any `(variant, iteration)` already marked complete; mid-batch failures (Ctrl-C, ffmpeg crash, network blip during fill) can be replayed without redoing finished work. Override the ledger location with `--checkpoint-dir`.

## Requirements

- Python 3.10+
- Windows `ffmpeg.exe` and `ffprobe.exe` on PATH when using the WSL-oriented workflow
- A `config.toml` copied from `config.example.toml` with paths for source media, `audio/`, `CTA/`, templates, and metadata CSV
- Ollama on `localhost:11434` only for `fill` and the full pipeline's best-effort metadata fill. Auto-launch is opt-in via `comfybulk fill --auto-launch-ollama`, `ollama.auto_launch = true`, or `COMFYBULK_AUTO_LAUNCH_OLLAMA=1`.

## Testing

```bash
pytest                       # unit tests; integration is excluded by default
COMFYBULK_REAL_TEST_CLIP=/path/to/clip.mp4 pytest -m integration
```

## License

MIT — see [LICENSE](LICENSE).
