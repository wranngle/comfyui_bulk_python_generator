"""LLM-driven metadata fill via local Ollama. Ports Fill-VideoMetadata.ps1.

For each row with a content_prompt: sends one Ollama request per empty field whose
prompt template exists in ai_metadata_prompts.csv. Detects existing duplicate values
across the CSV (per critical field), clears them, and regenerates with boosted
randomness (temp/top_p/repeat_penalty) to break out of LLM repetition.
"""
from __future__ import annotations
import csv, random, re, shutil, subprocess, time
from datetime import datetime
from pathlib import Path

import requests

from .config import Config
from .extract import remove_banned
from .ffmpeg import to_posix


SHORT_FIELDS = {"cover_text", "caption"}
NEVER_FILL = {"content_prompt", "seed", "clipname"}
EXCLUDED_FROM_DUPE_SCAN = NEVER_FILL


def _load_prompts(path: str) -> dict[str, str]:
    posix = to_posix(path)
    p: dict[str, str] = {}
    with open(posix, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            field = (row.get("field") or "").strip()
            tmpl = row.get("prompt") or ""
            if field and tmpl:
                p[field] = tmpl
    return p


def _read_csv(path: str) -> tuple[list[str], list[dict]]:
    posix = to_posix(path)
    with open(posix, "r", encoding="utf-8-sig", newline="") as f:
        rdr = csv.DictReader(f)
        return list(rdr.fieldnames or []), list(rdr)


def _write_csv(path: str, fieldnames: list[str], rows: list[dict], retries: int = 3, wait: int = 20):
    posix = to_posix(path)
    last = None
    for i in range(retries):
        try:
            with open(posix, "w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=fieldnames, quoting=csv.QUOTE_ALL)
                w.writeheader()
                for r in rows:
                    w.writerow({k: r.get(k, "") for k in fieldnames})
            return
        except (OSError, IOError) as e:
            last = e
            if i < retries - 1:
                time.sleep(wait)
    raise RuntimeError(f"CSV write failed: {last}")


def ensure_ollama(cfg: Config) -> None:
    """Try to reach Ollama; if not running, attempt to launch it locally."""
    try:
        r = requests.get(f"{cfg.ollama.host}/api/tags", timeout=5)
        if r.ok:
            return
    except requests.RequestException:
        pass
    # Try to start the Ollama desktop app on Windows.
    candidates = [
        Path("/mnt/c/Users/root/AppData/Local/Programs/Ollama/ollama app.exe"),
        Path("/mnt/c/Users/user/AppData/Local/Programs/Ollama/ollama app.exe"),
    ]
    for app in candidates:
        if app.exists():
            subprocess.Popen([str(app)])
            time.sleep(5)
            break


def ensure_model(cfg: Config) -> None:
    """If model is missing locally, create it from the configured GGUF."""
    if not shutil.which("ollama"):
        return
    r = subprocess.run(["ollama", "list"], capture_output=True, text=True)
    if cfg.ollama.model in (r.stdout or ""):
        return
    gguf = to_posix(cfg.ollama.gguf_path)
    if not Path(gguf).exists():
        return
    modelfile = Path("/tmp/comfybulk_modelfile.txt")
    modelfile.write_text(f'FROM "{cfg.ollama.gguf_path}"\n')
    subprocess.run(["ollama", "create", cfg.ollama.model, "-f", str(modelfile)], check=False)
    modelfile.unlink(missing_ok=True)


def _clean_response(txt: str) -> str:
    txt = re.sub(r"<think>.*?</think>", "", txt, flags=re.DOTALL)
    txt = re.sub(r"^/no_think\s*", "", txt)
    txt = re.sub(r"\r?\n", " ", txt)
    txt = re.sub(r"\s+", " ", txt)
    txt = re.sub(r"\*\*([^*]+)\*\*", r"\1", txt)
    txt = re.sub(r"^\*+\s*", "", txt)
    txt = re.sub(r"\s*\*+$", "", txt)
    txt = re.sub(r"^\s*[\*\-•]\s*", "", txt)
    if m := re.search(r'"([^"]+)"', txt):
        txt = m.group(1)
    return txt.strip()


def ollama_generate(cfg: Config, prompt: str, *, temp: float, top_p: float, repeat_penalty: float, num_predict: int = 200) -> str:
    body = {
        "model": cfg.ollama.model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temp, "top_p": top_p, "repeat_penalty": repeat_penalty, "num_predict": num_predict},
    }
    r = requests.post(f"{cfg.ollama.host}/api/generate", json=body, timeout=600)
    r.raise_for_status()
    return _clean_response(r.json().get("response", ""))


def _scan_duplicates(rows: list[dict], llm_fields: set[str]) -> dict[tuple[int, str], bool]:
    """Mark (row_index, field) pairs whose value duplicates another row in the same field.
    Empties duplicate values in-place so the regen loop reruns them."""
    dupes: dict[tuple[int, str], bool] = {}
    for f in llm_fields:
        if f in EXCLUDED_FROM_DUPE_SCAN or f in SHORT_FIELDS:
            continue
        counts: dict[str, int] = {}
        for r in rows:
            v = r.get(f)
            if v and v.strip():
                counts[v] = counts.get(v, 0) + 1
        for i, r in enumerate(rows):
            v = r.get(f)
            if v and counts.get(v, 0) > 1:
                r[f] = ""
                dupes[(i, f)] = True
    return dupes


def fill(cfg: Config) -> int:
    ensure_ollama(cfg)
    ensure_model(cfg)
    prompts = _load_prompts(cfg.paths.ai_prompts_csv)
    fieldnames, rows = _read_csv(cfg.paths.metadata_csv)

    # Backup CSV
    bk = Path(to_posix(cfg.paths.metadata_csv)).with_suffix(
        f".bak.{datetime.now().strftime('%Y%m%d%H%M%S')}.csv")
    shutil.copy(to_posix(cfg.paths.metadata_csv), bk)

    # Retroactive cleanup: banned terms in LLM-filled fields.
    cleanable = {"filename", "title", "description", "caption", "cover_text",
                 "pinned_comment", "CTA", "tags", "content_prompt"}
    n_clean = 0
    for r in rows:
        for f in cleanable:
            v = r.get(f)
            if v:
                cleaned = remove_banned(v)
                if cleaned != v:
                    r[f] = cleaned
                    n_clean += 1
    if n_clean:
        _write_csv(cfg.paths.metadata_csv, fieldnames, rows)

    dupes = _scan_duplicates(rows, set(prompts.keys()))
    if dupes:
        _write_csv(cfg.paths.metadata_csv, fieldnames, rows)

    filled = 0
    for i, r in enumerate(rows):
        if not r.get("content_prompt"):
            continue
        for f in fieldnames:
            if r.get(f) or f in NEVER_FILL or f not in prompts:
                continue
            was_dup = dupes.get((i, f), False)
            temp = 1.8 if was_dup else 0.8
            top_p = 0.85 if was_dup else 0.95
            rp = 1.3 if was_dup else 1.1
            ctx_len = 500 if was_dup else 200
            ctx = remove_banned(r["content_prompt"][:ctx_len])
            unique = f"Row: {i+1}" + (f" | Seed: {r['seed']}" if r.get("seed") else "")
            if was_dup:
                unique += f" | UNIQUENESS REQUIRED: Variation #{random.randint(1000, 9999)}"
            anti = ("\n\n[CRITICAL: This is a RE-GENERATION. Previous output was duplicate. "
                    "You MUST produce a substantially different, creative variation. Use unexpected "
                    "word choices, different phrasing, alternative angles. DO NOT repeat common patterns.]"
                    if was_dup else "")
            banned = ("\n\n[BANNED TERMS: Never use: DMT, psychedelic, trip, ego-death. "
                      "Replace with: visionary, abstract, journey, transcendent.]")
            prompt = f"/no_think\n\nContext: {ctx}...\n{unique}{anti}{banned}\n\nTask: {prompts[f]}"
            try:
                txt = remove_banned(ollama_generate(cfg, prompt, temp=temp, top_p=top_p, repeat_penalty=rp))
            except Exception as e:
                print(f"[LLM ERROR] row {i+1} field {f}: {e}")
                continue
            if not txt:
                continue
            r[f] = txt
            _write_csv(cfg.paths.metadata_csv, fieldnames, rows)
            filled += 1
    return filled
