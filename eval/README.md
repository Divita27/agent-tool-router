# Test data (Step 7)

The test set is **real people speaking**, run through Sarvam speech-to-text, so results reflect real Hindi, Hinglish and English speech, including speech-to-text mistakes.

## Files

| File | What it is | In git? |
|---|---|---|
| `scenarios.py` | 58 scenarios (46 single requests + 12 conversations, 71 turns) with labels | yes |
| `recording_kit/s01.html` … | One card sheet per recorder, grouped by persona | yes |
| `recording_kit/assignments.json` | Which scenarios each speaker records | yes |
| `data/audio/` | The recordings | **no** (people's voices) |
| `data/speakers.csv` | Who each speaker is, and that they agreed | **no** (names) |
| `data/transcripts.jsonl` | Speech-to-text output | yes |
| `data/test_set.jsonl` | Transcripts joined with labels, split by speaker | yes |
| `data/dev_typed.jsonl` | One phrasing per turn typed by Claude. **Router development only, never reported as results** | yes |

## Workflow

1. **Find 10 recorders.** Friends, family, classmates. Mix of Hindi, English and Hinglish speakers is ideal.
2. **Send each person their sheet** (`recording_kit/s01.html` for speaker 1, and so on). Each sheet is ~30 clips, about 12 minutes.
3. **Write down who is who** in `data/speakers.csv` (copy `speakers_template.csv`). Only record people who said yes.
4. **Collect the files** into `data/audio/`. Check names look like `s03_S12.m4a` or `s03_M04_t2.m4a`.
5. **Transcribe:**
   ```bash
   python -m eval.transcribe --dry-run    # check names, count clips, see the cost
   python -m eval.transcribe              # about ₹15 for 300 clips
   ```
6. **Build the test set:**
   ```bash
   python -m eval.build_test_set
   ```
   It reports missing clips per speaker, so you know who to chase.
7. **Spot-check 20 transcripts by listening.** If a label is wrong for what someone actually said (e.g. they asked a different question), fix it in `scenarios.py` or drop the clip, and note it.

## Conversations

Recorders can't talk to a live agent, so each conversation card shows a **fixed agent reply** between turns (written from the real backend data). The speaker reads it, then records their next turn.

The benchmark uses those same replies as the conversation history: turn 2 is tested with *turn 1 transcript + scripted reply* before it. So every recorded turn is judged in exactly the context the speaker was answering. The live agent's own replies are only used in the end-to-end demo.

## Rules that keep the results honest

- **Speakers s05 and s10 are the final test set.** Don't read their transcripts while building the router. Run them once, in Step 11.
- `dev_typed.jsonl` and the tool examples in `tools/catalog.py` were written by Claude. Use them to build, never to report results.
- Coverage: the scenarios test 62 of the 111 tools. Results say nothing about the other 49; say so in the write-up.
