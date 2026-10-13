"""Make one printable card sheet per recorder.

Run from the project folder:
    python -m eval.make_cards --speakers 10

Writes eval/recording_kit/s01.html ... s10.html and assignments.json.
Each single-turn scenario goes to 5 different speakers, each conversation to 4.
"""
import argparse
import html
import json
from pathlib import Path

from eval.scenarios import PERSONAS, SCENARIOS, TODAY_NOTE

KIT = Path(__file__).parent / "recording_kit"
SINGLE_COPIES, MULTI_COPIES = 5, 4


def assign(n_speakers):
    """Spread copies of each scenario over different speakers, round-robin."""
    sheets = {f"s{i + 1:02d}": [] for i in range(n_speakers)}
    names = list(sheets)
    slot = 0
    for copies, group in ((SINGLE_COPIES, [s for s in SCENARIOS if not s.multi_turn]),
                          (MULTI_COPIES, [s for s in SCENARIOS if s.multi_turn])):
        for s in group:
            for _ in range(min(copies, n_speakers)):
                sheets[names[slot % n_speakers]].append(s.id)
                slot += 1
    return sheets


CSS = """
body { font-family: -apple-system, system-ui, sans-serif; max-width: 720px; margin: 24px auto; padding: 0 16px;
       color: #1a1a19; background: #fcfcfb; line-height: 1.45; }
h1 { font-size: 22px; margin-bottom: 4px; }
.intro { background: #f1f0ec; border-radius: 10px; padding: 14px 18px; margin: 16px 0 24px; }
.intro li { margin: 4px 0; }
.card { border: 1px solid #d9d8d2; border-radius: 10px; padding: 14px 18px; margin: 14px 0; break-inside: avoid; }
.file { font-family: ui-monospace, Menlo, monospace; font-weight: 600; background: #e8f0fb; color: #1b4f8f;
        padding: 2px 8px; border-radius: 6px; }
h2 { font-size: 18px; margin: 32px 0 6px; }
.persona { background: #f6f5f1; border-left: 4px solid #2a78d6; padding: 10px 14px; border-radius: 6px; }
.turn { margin: 10px 0 0; padding-left: 12px; border-left: 3px solid #d9d8d2; }
.situation { font-size: 17px; }
.reply { margin: 8px 0 8px 12px; padding: 8px 12px; background: #f1f0ec; border-radius: 8px; color: #3b3a37;
         font-style: italic; }
.reply span { font-style: normal; font-weight: 600; color: #52514e; }
code { font-family: ui-monospace, Menlo, monospace; }
@media print { .card { page-break-inside: avoid; } }
"""

INTRO = """
<div class="intro">
<p><strong>Thank you for helping!</strong> You'll play a customer calling a loan company's phone helpline.
Each card describes a situation. For each one, record yourself saying what you'd actually say.</p>
<ul>
<li><strong>Don't read the card out loud.</strong> Say it the way you'd naturally talk on a phone call.</li>
<li>Use whatever language feels natural: Hindi, English, Hinglish or a mix. Short or long is fine.</li>
<li>Use your phone's voice recorder. One clip per card (or per turn). <strong>Keep each clip under 25 seconds.</strong></li>
<li>Record somewhere normal: a quiet room is good, a bit of background noise is fine too.</li>
<li><strong>Name each file</strong> exactly as shown in blue, e.g. <code>{example}</code>. Keep the phone's file type (.m4a is fine).</li>
<li><strong>Conversations:</strong> record each turn as a separate clip. Between turns, read the agent's reply
on the card (in grey), then record what you'd say back to it.</li>
<li>The names, loans and numbers are made up. <strong>Never say your real details.</strong></li>
<li>{today}</li>
</ul>
<p>When you're done, send all the files back (Google Drive, WhatsApp as documents, or AirDrop).
The recordings are only used to test a voice agent for a portfolio project.</p>
</div>
"""


def persona_html(customer):
    name, persona = PERSONAS[customer]
    return f'<h2>Playing {html.escape(name)}</h2><div class="persona">{html.escape(persona)}</div>'


def card_html(speaker, s):
    parts = ['<div class="card">']
    if s.multi_turn:
        parts.append(f"<div><strong>Conversation {s.id}</strong> — {len(s.turns)} clips</div>")
        for i, t in enumerate(s.turns, 1):
            parts.append(f'<div class="turn"><span class="file">{speaker}_{s.id}_t{i}</span>'
                         f'<div class="situation">{html.escape(t.situation)}</div></div>')
            if t.agent_reply:
                parts.append(f'<div class="reply"><span>Agent replies:</span> “{html.escape(t.agent_reply)}”</div>')
    else:
        parts.append(f'<span class="file">{speaker}_{s.id}</span>'
                     f'<div class="situation">{html.escape(s.turns[0].situation)}</div>')
    parts.append("</div>")
    return "".join(parts)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--speakers", type=int, default=10)
    args = parser.parse_args()

    by_id = {s.id: s for s in SCENARIOS}
    sheets = assign(args.speakers)
    KIT.mkdir(exist_ok=True)
    for speaker, ids in sheets.items():
        clips = sum(len(by_id[i].turns) for i in ids)
        ids = sorted(ids, key=lambda i: (by_id[i].customer, by_id[i].multi_turn, i))
        cards, current = [], None
        for i in ids:
            if by_id[i].customer != current:
                current = by_id[i].customer
                cards.append(persona_html(current))
            cards.append(card_html(speaker, by_id[i]))
        cards = "".join(cards)
        page = (f"<!doctype html><html><head><meta charset='utf-8'>"
                f"<meta name='viewport' content='width=device-width, initial-scale=1'>"
                f"<title>Recording cards {speaker}</title><style>{CSS}</style></head><body>"
                f"<h1>Recording cards — speaker {speaker}</h1><div>{len(ids)} cards, {clips} clips, about "
                f"{round(clips * 0.4)} minutes</div>"
                + INTRO.format(example=f"{speaker}_{ids[0]}", today=TODAY_NOTE) + cards + "</body></html>")
        (KIT / f"{speaker}.html").write_text(page)
    (KIT / "assignments.json").write_text(json.dumps(sheets, indent=2))

    clips = {sp: sum(len(by_id[i].turns) for i in ids) for sp, ids in sheets.items()}
    print(f"Made {len(sheets)} card sheets in {KIT.relative_to(Path.cwd())}/")
    print(f"Clips per speaker: {min(clips.values())}-{max(clips.values())}, total {sum(clips.values())}")


if __name__ == "__main__":
    main()
