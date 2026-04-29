# comfyui_bulk_python_generator

Bulk post-processing pipeline for ComfyUI-generated video. Selects clips, builds layout variants (grid / montage / single / cta_only), runs ffmpeg effects (glitch, reverse, rainbow border, motion blur), mixes random audio, and fills metadata via a local Ollama LLM.

Ported from a tangle of PowerShell scripts into a single Python package. Designed to run in WSL while shelling out to Windows `ffmpeg.exe`/`ffprobe.exe` against media on `D:\`.

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
comfybulk pipeline --source D:\\ComfyUI\\ComfyUI\\output\\favorites\\assemblymaker --variant grid

# multiple variants and quantities (grid + montage, 3 each)
comfybulk pipeline --source D:\\... --variant grid --variant montage --quantity 3

# clean variant only (no effects stack — like the legacy create_*.ps1 scripts)
comfybulk pipeline --source D:\\... --variant single --no-effects

# extract metadata only
comfybulk extract --path D:\\ComfyUI\\ComfyUI\\output\\favorites\\assemblymaker

# fill empty LLM fields
comfybulk fill

# organize media by prompt
comfybulk organize --path D:\\ComfyUI\\ComfyUI\\output\\favorites

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

The bulk pipeline applies the full effects stack (glitch → reverse → rainbow → motion blur) by default. With `--no-effects`, the variant is built clean with both *with-CTA* and *without-CTA* outputs (matches what the legacy `create_*` PowerShell scripts did).

## Requirements

- WSL (Ubuntu) with Python 3.10+
- Windows `ffmpeg.exe` and `ffprobe.exe` on PATH (accessible from WSL)
- Ollama running on `localhost:11434` with model `josiefied-qwen3-8b:latest` (auto-launches if installed)
- Source media + `audio/` + `CTA/` folders on `D:\` (or any reachable path)

## Testing

```bash
pytest                       # unit tests only
pytest -m integration        # also runs end-to-end against real media on D:\
```

## License

MIT — see [LICENSE](LICENSE).
