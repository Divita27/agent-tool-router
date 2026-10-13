"""Run recordings through Sarvam speech-to-text (Saaras v4).

Put recordings in eval/data/audio/ named like  s03_S12.m4a  or  s03_M04_t2.m4a, then:

    python -m eval.transcribe --dry-run          # count files and estimate cost
    python -m eval.transcribe                    # transcribe (asks before spending)
    python -m eval.transcribe --mode translit    # a second pass in another output mode

Results go to eval/data/transcripts.jsonl. Files already transcribed in that mode are skipped.
Price: ₹30 per hour of audio, billed per second.
"""
import argparse
import json
import re
import subprocess
from pathlib import Path

import requests

DATA = Path(__file__).parent / "data"
AUDIO_DIR = DATA / "audio"
OUT = DATA / "transcripts.jsonl"
URL = "https://api.sarvam.ai/speech-to-text"
MODEL = "saaras:v4"
PRICE_PER_HOUR = 30
MAX_SECONDS = 30
NAME = re.compile(r"^(s\d{2})_([SM]\d{2})(?:_t(\d))?\.(m4a|wav|mp3|aac|ogg|flac|webm)$", re.IGNORECASE)
MIME = {"m4a": "audio/mp4", "wav": "audio/wav", "mp3": "audio/mpeg", "aac": "audio/aac",
        "ogg": "audio/ogg", "flac": "audio/flac", "webm": "audio/webm"}


def duration_seconds(path):
    """Audio length via macOS afinfo. Falls back to a 7-second guess elsewhere."""
    try:
        out = subprocess.run(["afinfo", str(path)], capture_output=True, text=True, timeout=10).stdout
        m = re.search(r"estimated duration: ([\d.]+) sec", out)
        if m:
            return float(m.group(1))
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return 7.0


def parse(path):
    m = NAME.match(path.name)
    if not m:
        return None
    speaker, scenario, turn, ext = m.groups()
    return {"speaker": speaker.lower(), "scenario": scenario.upper(), "turn": int(turn or 1), "ext": ext.lower()}


def done_keys():
    if not OUT.exists():
        return set()
    return {(r["file"], r["mode"]) for r in map(json.loads, OUT.read_text().splitlines())}


def transcribe(path, mode, api_key, ext):
    with open(path, "rb") as f:
        r = requests.post(URL, headers={"api-subscription-key": api_key},
                          files={"file": (path.name, f, MIME[ext])},
                          data={"model": MODEL, "mode": mode}, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"{r.status_code}: {r.text[:200]}")
    return r.json()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default="codemix", choices=["transcribe", "codemix", "translit", "translate",
                                                               "verbatim"])
    parser.add_argument("--audio-dir", type=Path, default=AUDIO_DIR)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()

    files = sorted(p for p in args.audio_dir.glob("*") if p.is_file() and not p.name.startswith("."))
    bad = [p.name for p in files if not parse(p)]
    good = [p for p in files if parse(p)]
    done = done_keys()
    todo = [p for p in good if (p.name, args.mode) not in done]
    lengths = {p: duration_seconds(p) for p in todo}
    too_long = [p.name for p, s in lengths.items() if s > MAX_SECONDS]
    todo = [p for p in todo if lengths[p] <= MAX_SECONDS]
    seconds = sum(lengths[p] for p in todo)

    print(f"{len(files)} files in {args.audio_dir}: {len(good)} named correctly, {len(done)} already done")
    if bad:
        print(f"  Skipping {len(bad)} badly named file(s), rename them like s03_S12.m4a: {bad[:10]}")
    if too_long:
        print(f"  Skipping {len(too_long)} clip(s) over {MAX_SECONDS}s (API limit): {too_long[:10]}")
    print(f"To transcribe in '{args.mode}' mode: {len(todo)} clips, {seconds / 60:.1f} minutes of audio, "
          f"about ₹{seconds / 3600 * PRICE_PER_HOUR:.2f}")
    if args.dry_run or not todo:
        return
    if not args.yes and input("Go ahead? [y/N] ").strip().lower() != "y":
        return

    from agent.sarvam_client import API_KEY

    DATA.mkdir(exist_ok=True)
    failed = 0
    with open(OUT, "a") as out:
        for i, p in enumerate(todo, 1):
            info = parse(p)
            try:
                r = transcribe(p, args.mode, API_KEY, info["ext"])
            except Exception as e:
                failed += 1
                print(f"  [{i}/{len(todo)}] {p.name} FAILED: {e}")
                continue
            record = {"file": p.name, "speaker": info["speaker"], "scenario": info["scenario"], "turn": info["turn"],
                      "mode": args.mode, "model": MODEL, "transcript": r.get("transcript", ""),
                      "language_code": r.get("language_code"), "seconds": round(lengths[p], 1)}
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            out.flush()
            print(f"  [{i}/{len(todo)}] {p.name} ({record['language_code']}): {record['transcript']}")
    print(f"Done. {len(todo) - failed} transcribed, {failed} failed (run again to retry them).")


if __name__ == "__main__":
    main()
