"""Join transcripts with scenario labels to make the labelled test data.

Run from the project folder:  python -m eval.build_test_set

Writes:
  eval/data/dev_typed.jsonl   Claude's typed phrasings. For building the router ONLY, never for results.
  eval/data/test_set.jsonl    Real recordings. Split by SPEAKER:
                                 split="dev"   speakers you may look at while tuning the router
                                 split="test"  held-out speakers, run ONCE at the end (Step 11)

Every record has the same shape, so the router and benchmark code work on both files.
"""
import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from eval.scenarios import SCENARIOS, SCENARIOS_BY_ID

DATA = Path(__file__).parent / "data"
KIT = Path(__file__).parent / "recording_kit"


def is_test_speaker(speaker):
    """Every 5th speaker (s05, s10, s15, ...) is held out: about 20%."""
    return int(speaker[1:]) % 5 == 0


def record(rid, speaker, scenario, turn_no, text, split, source, language=None, audio=None):
    turn = scenario.turns[turn_no - 1]
    return {
        "id": rid, "conversation": f"{speaker}_{scenario.id}", "speaker": speaker, "scenario": scenario.id,
        "turn": turn_no, "turns_in_conversation": len(scenario.turns), "customer": scenario.customer,
        "text": text, "language": language, "audio": audio, "source": source, "split": split,
        "agent_reply": turn.agent_reply or None,  # fixed reply after this turn, used as history for the next
        "tools": turn.tools, "outcome": turn.outcome, "args": turn.args,
        "tags": sorted(set(scenario.tags + turn.tags)),
    }


def write(path, records):
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records))


def build_dev():
    records = [record(f"claude_{s.id}_t{i}", "claude", s, i, t.dev_text, "dev_typed", "typed_by_claude")
               for s in SCENARIOS for i, t in enumerate(s.turns, 1)]
    write(DATA / "dev_typed.jsonl", records)
    print(f"dev_typed.jsonl: {len(records)} typed turns (router development only)")


def build_test(mode):
    path = DATA / "transcripts.jsonl"
    if not path.exists():
        print("test_set.jsonl: no transcripts yet. Record, then run python -m eval.transcribe")
        return
    rows = [r for r in map(json.loads, path.read_text().splitlines()) if r["mode"] == mode]
    records, unknown = [], []
    for r in rows:
        s = SCENARIOS_BY_ID.get(r["scenario"])
        if not s or r["turn"] > len(s.turns):
            unknown.append(r["file"])
            continue
        split = "test" if is_test_speaker(r["speaker"]) else "dev"
        records.append(record(f"{r['speaker']}_{s.id}_t{r['turn']}", r["speaker"], s, r["turn"], r["transcript"],
                              split, f"recording_{mode}", r["language_code"], r["file"]))
    records.sort(key=lambda r: (r["speaker"], r["scenario"], r["turn"]))

    # A conversation is only usable for multi-turn tests if every turn was recorded
    turns_seen = defaultdict(set)
    for r in records:
        turns_seen[r["conversation"]].add(r["turn"])
    for r in records:
        r["conversation_complete"] = len(turns_seen[r["conversation"]]) == r["turns_in_conversation"]
    write(DATA / "test_set.jsonl", records)

    print(f"test_set.jsonl: {len(records)} turns from {len({r['speaker'] for r in records})} speakers "
          f"({mode} transcripts)")
    print("  split:", dict(Counter(r["split"] for r in records)))
    print("  language detected:", dict(Counter(r["language"] for r in records)))
    if unknown:
        print(f"  ignored {len(unknown)} file(s) with unknown scenario/turn: {unknown[:10]}")
    incomplete = sorted({r["conversation"] for r in records if not r["conversation_complete"]})
    if incomplete:
        print(f"  incomplete conversations (a turn is missing): {incomplete}")
    assignments = KIT / "assignments.json"
    if assignments.exists():
        expected = {f"{sp}_{sid}_t{i}" for sp, ids in json.loads(assignments.read_text()).items()
                    for sid in ids for i in range(1, len(SCENARIOS_BY_ID[sid].turns) + 1)}
        recorded_speakers = {r["speaker"] for r in records}
        missing = sorted(x for x in expected - {r["id"] for r in records} if x[:3] in recorded_speakers)
        if missing:
            print(f"  {len(missing)} assigned clip(s) not received yet from these speakers: {missing[:12]}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default="codemix", help="which transcript mode to use")
    args = parser.parse_args()
    DATA.mkdir(exist_ok=True)
    build_dev()
    build_test(args.mode)


if __name__ == "__main__":
    main()
