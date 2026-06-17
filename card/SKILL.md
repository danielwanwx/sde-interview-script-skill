---
name: card
description: "Turn any pasted text, article excerpt, study note, interview topic, system design/API note, or explanation request into a concise speakable script embedded in an Excalidraw-style visual card, with image-and-link-only replies by default. Use when the user says use $card, make a card, draw an Excalidraw card, create a script card, summarize this into a visual note, prepare an interview answer, or wants a short explainable version of arbitrary text."
---

# Card

## Goal

Turn the user's text into a clean Excalidraw-style script card. The card should contain the actual explanation, not empty boxes. Default to English unless the user specifies Chinese or another language.

Use this skill for general text too. If the user asks for SDE, system design, API design, or interview prep, frame the answer as a senior SDE interview response. Otherwise, frame it as a clear teachable explanation or talk track.

## Output Contract

Default chat response:

1. Rendered preview image when the host can display it.
2. Excalidraw link if upload succeeds; otherwise the `.excalidraw` path.

Do not paste the script text outside the image unless the user asks for copyable text.

## Content Shape

Create one compact JSON object:

```json
{
  "title": "Short title",
  "language": "English",
  "summary": "One short sentence explaining the core idea.",
  "script": "A concise speakable explanation in the target language, usually 2 short paragraphs.",
  "short": "A 30-second version in the target language, max 2 sentences.",
  "flows": [
    "Signal / context",
    "Core idea",
    "Tradeoff / caveat",
    "Takeaway"
  ]
}
```

Language rules:

- Default to English.
- Use Chinese only when the user writes `Chinese`, `用中文`, or clearly asks for Chinese output.
- Use any other requested language directly.
- For bilingual output, keep each language short enough to fit the card.

## Rendering

Use the bundled renderer first:

```bash
python3 scripts/render_interview_card.py --content /tmp/card.json --out /tmp/card-output --slug card
```

If the current working directory is not this skill directory, run the script with its absolute path. Read the JSON emitted by the script; it contains `preview`, `excalidraw`, `link`, and `share`.

Host-specific delivery:

- Codex/Cursor: return Markdown image for `preview`, then `link` or `.excalidraw` path.
- Claude Code or terminal-only hosts: return `link` first; if no link exists, return `preview` and `.excalidraw` paths.

## Visual Style

- White background.
- Transparent card and flow-box backgrounds.
- Black/dark strokes for title, summary, script, and 30-second section.
- Blue strokes/text/arrows only for the decision-flow boxes.
- Handwritten Excalidraw feel.
- Generous spacing and readable line breaks.

## Quality Bar

- Be concise and speakable.
- Preserve the user's intent instead of covering every detail.
- Add one practical example or caveat only when it makes the explanation more solid.
- For technical interview content, show senior judgment through tradeoffs, production implications, and boundary conditions.
