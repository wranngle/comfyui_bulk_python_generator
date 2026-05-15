"""CLI entry: `comfybulk <subcommand>`."""
from __future__ import annotations
import argparse, sys
from pathlib import Path

from .config import load


def cmd_pipeline(a):
    from .pipeline import run
    cfg = load(Path(a.config) if a.config else None)
    outs = run(a.variant, a.source, quantity=a.quantity, cfg=cfg,
               audio_source=a.audio_source, reversal_speed=a.reversal_speed,
               no_effects=a.no_effects, organize_favorites=a.organize,
               seed=a.seed, write_manifest=a.write_manifest,
               resume=a.resume, checkpoint_dir=a.checkpoint_dir)
    print(f"\n=== Generated {len(outs)} output(s) ===")
    for o in outs:
        print(f"  {o}")


def cmd_extract(a):
    from .extract import process_file, process_directory
    cfg = load(Path(a.config) if a.config else None)
    csv_path = a.csv or cfg.paths.metadata_csv
    p = Path(a.path)
    if p.is_dir():
        ok, fail = process_directory(str(p), csv_path, test_mode=a.test_mode)
        print(f"Extracted {ok}, failed {fail}")
    elif p.is_file():
        added = process_file(str(p), csv_path, test_mode=a.test_mode)
        print(f"{'Added' if added else 'Skipped'}: {p.name}")
    else:
        print(f"Path not found: {a.path}", file=sys.stderr)
        sys.exit(1)


def cmd_images(a):
    from .images import process_directory
    cfg = load(Path(a.config) if a.config else None)
    csv_path = a.csv or cfg.paths.metadata_csv
    p = Path(a.path)
    if not p.is_dir():
        print(f"Not a directory: {a.path}", file=sys.stderr)
        sys.exit(1)
    ok, fail = process_directory(str(p), csv_path, test_mode=a.test_mode)
    print(f"Images: {ok} row(s) written, {fail} failed")


def cmd_fill(a):
    from .fill import fill
    cfg = load(Path(a.config) if a.config else None)
    if a.auto_launch_ollama:
        cfg.ollama.auto_launch = True
    n = fill(cfg)
    print(f"Filled {n} field(s)")


def cmd_organize(a):
    from .organize import organize
    cfg = load(Path(a.config) if a.config else None)
    path = a.path or cfg.paths.favorites_root
    r = organize(path, test_mode=a.test_mode)
    print(f"Groups {r['groups']} | folders {len(r['created'])} | moved {r['moved']}")


def cmd_glitch(a):
    from .effects import apply_glitch_negative
    cfg = load(Path(a.config) if a.config else None)
    out = apply_glitch_negative(a.input, a.output_dir,
                                neg_audio=a.neg_audio,
                                neg_audio_dir=a.neg_audio_dir or cfg.paths.neg_audio)
    print(f"Output: {out}")


def cmd_rainbow(a):
    from .effects import rainbow_apply, rainbow_generate
    cfg = load(Path(a.config) if a.config else None)
    if a.action == "generate":
        out = rainbow_generate(cfg.paths.templates)
        print(f"Template: {out}")
    else:
        if not a.input or not a.output:
            print("--input and --output required for apply", file=sys.stderr)
            sys.exit(2)
        out = rainbow_apply(a.input, a.output, cfg.paths.templates)
        print(f"Output: {out}")


def cmd_reverse(a):
    from .effects import reverse_ending
    out = reverse_ending(a.input, a.output_dir, reverse_duration=a.duration)
    print(f"Output: {out}")


def cmd_mixaudio(a):
    from .audio import mix_random_audio
    cfg = load(Path(a.config) if a.config else None)
    out = mix_random_audio(a.input, a.audio_folder or cfg.paths.audio_folder,
                           output_video=a.output, audio_volume=a.audio_volume,
                           original_audio_volume=a.original_volume)
    print(f"Output: {out}")


def cmd_pingpong(a):
    from .tools import fastpingpong
    out = fastpingpong(a.input, speed_percent=a.speed,
                       exponential=a.exponential, exponential_base=a.exp_base,
                       output=a.output)
    print(f"Output: {out}")


def cmd_timestretch(a):
    from .tools import timestretch
    out = timestretch(a.input, stretch=a.stretch, no_pingpong=a.no_pingpong,
                      target_fps=a.fps, output=a.output)
    print(f"Output: {out}")


def cmd_convert(a):
    from .tools import convert_to_mp4
    out = convert_to_mp4(a.input, output_file=a.output, duration=a.duration, fps=a.fps)
    print(f"Output: {out}")


def cmd_export(a):
    if a.target != "shopify":
        print(f"Unknown export target: {a.target}", file=sys.stderr)
        sys.exit(2)
    from .export.shopify import ShopifyDefaults, asset_count, export_shopify_csv
    defaults = ShopifyDefaults(
        vendor=a.vendor,
        product_type=a.product_type,
        price=a.price,
    )
    n = export_shopify_csv(a.manifest, a.output, defaults=defaults)
    expected = asset_count(a.manifest)
    print(f"Wrote {n} row(s) to {a.output} (manifest assets: {expected})")


def main():
    p = argparse.ArgumentParser(prog="comfybulk", description="ComfyUI bulk media pipeline")
    p.add_argument("--config", help="Path to config.toml (default: search ./, repo root, $COMFYBULK_CONFIG)")
    sub = p.add_subparsers(dest="cmd", required=True)

    from .cli.recipes import add_subparser as _add_recipes
    _add_recipes(sub)

    sp = sub.add_parser("pipeline", help="Full bulk processing pipeline")
    sp.add_argument("--source", required=True, help="Source folder OR specific .mp4 file")
    sp.add_argument("--variant", choices=["grid", "montage", "single", "cta_only"],
                    action="append", required=True, help="May be repeated")
    sp.add_argument("--quantity", type=int, default=1)
    sp.add_argument("--audio-source", default=None)
    sp.add_argument("--reversal-speed", type=float, default=4.0)
    sp.add_argument("--no-effects", action="store_true",
                    help="Skip effects stack; output clean dual cta/nocta variants")
    sp.add_argument("--organize", action="store_true",
                    help="Opt in to organizing cfg.paths.favorites_root before processing")
    sp.add_argument("--seed", type=int,
                    help="Seed pipeline clip/CTA/audio choices for reproducible selection")
    sp.add_argument("--write-manifest", action="store_true",
                    help="Append run metadata to finals/pipeline_manifest.jsonl")
    sp.add_argument("--resume", action="store_true",
                    help="Skip iterations already recorded in the checkpoint ledger")
    sp.add_argument("--checkpoint-dir", default=None,
                    help="Directory holding .comfybulk-checkpoint.jsonl (default: source folder)")
    sp.set_defaults(func=cmd_pipeline)

    sp = sub.add_parser("extract", help="Extract ComfyUI metadata into metadata.csv")
    sp.add_argument("--path", required=True)
    sp.add_argument("--csv", help="Override metadata.csv path")
    sp.add_argument("--test-mode", action="store_true", help="Don't write to CSV")
    sp.set_defaults(func=cmd_extract)

    sp = sub.add_parser("images", help="Bulk-process a directory of PNG/JPG images into metadata.csv")
    sp.add_argument("path", help="Directory of images (recursive). Non-image files are skipped.")
    sp.add_argument("--csv", help="Override metadata.csv path")
    sp.add_argument("--test-mode", action="store_true", help="Count rows but don't write CSV")
    sp.set_defaults(func=cmd_images)

    sp = sub.add_parser("fill", help="Fill empty LLM-generated CSV fields via Ollama")
    sp.add_argument("--auto-launch-ollama", action="store_true",
                    help="Opt in to launching `ollama serve` if Ollama is not reachable")
    sp.set_defaults(func=cmd_fill)

    sp = sub.add_parser("organize", help="Group media into prompt-named subfolders")
    sp.add_argument("--path", help="Defaults to config favorites_root")
    sp.add_argument("--test-mode", action="store_true")
    sp.set_defaults(func=cmd_organize)

    sp = sub.add_parser("glitch", help="Apply phased negative-flash glitch effect")
    sp.add_argument("--input", required=True)
    sp.add_argument("--output-dir", required=True)
    sp.add_argument("--neg-audio", help="Specific glitch audio file")
    sp.add_argument("--neg-audio-dir", help="Override negatives folder")
    sp.set_defaults(func=cmd_glitch)

    sp = sub.add_parser("rainbow", help="Generate or apply rainbow border overlay")
    sp.add_argument("--action", choices=["generate", "apply"], default="apply")
    sp.add_argument("--input")
    sp.add_argument("--output")
    sp.set_defaults(func=cmd_rainbow)

    sp = sub.add_parser("reverse", help="Reverse the last 20 percent (or custom segment) of a video")
    sp.add_argument("--input", required=True)
    sp.add_argument("--output-dir", required=True)
    sp.add_argument("--duration", type=float, default=0.0,
                    help="Seconds to reverse (0 = auto, 20 percent of input)")
    sp.set_defaults(func=cmd_reverse)

    sp = sub.add_parser("mixaudio", help="Mix random audio with platform-ready chain")
    sp.add_argument("--input", required=True)
    sp.add_argument("--audio-folder")
    sp.add_argument("--output")
    sp.add_argument("--audio-volume", type=float, default=0.6)
    sp.add_argument("--original-volume", type=float, default=0.4)
    sp.set_defaults(func=cmd_mixaudio)

    sp = sub.add_parser("pingpong", help="Fast forward-reverse loops")
    sp.add_argument("--input", required=True)
    sp.add_argument("--speed", type=int, default=1000, help="Speed percent (>100)")
    sp.add_argument("--exponential", action="store_true")
    sp.add_argument("--exp-base", type=float, default=1.2)
    sp.add_argument("--output")
    sp.set_defaults(func=cmd_pingpong)

    sp = sub.add_parser("timestretch", help="Stretch video duration with optional ping-pong")
    sp.add_argument("--input", required=True)
    sp.add_argument("--stretch", type=float, default=2.0)
    sp.add_argument("--no-pingpong", action="store_true")
    sp.add_argument("--fps", type=int, default=60)
    sp.add_argument("--output")
    sp.set_defaults(func=cmd_timestretch)

    sp = sub.add_parser("convert", help="WebP/WebM → MP4")
    sp.add_argument("--input", required=True)
    sp.add_argument("--output")
    sp.add_argument("--duration", type=float, default=5.0, help="WebP only")
    sp.add_argument("--fps", type=int, default=30, help="WebP only")
    sp.set_defaults(func=cmd_convert)

    sp = sub.add_parser("export", help="Export pipeline outputs into downstream surfaces (Shopify)")
    sp.add_argument("--target", choices=["shopify"], default="shopify")
    sp.add_argument("--manifest", required=True, help="Path to finals/pipeline_manifest.jsonl")
    sp.add_argument("--output", required=True, help="Destination CSV path")
    sp.add_argument("--vendor", default="Wranngle")
    sp.add_argument("--product-type", default="Generative Video")
    sp.add_argument("--price", default="0.00")
    sp.set_defaults(func=cmd_export)

    a = p.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
