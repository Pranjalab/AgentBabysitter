#!/usr/bin/env python3
"""Speak text as a Telegram-ready voice note.

Usage: speak.py "text" [out.ogg] [--exag 0.5] [--cfg 0.5] [--cpu]
       speak.py - out.ogg < story.txt        # "-" reads stdin

--exag is the emotion dial: 0.3 flat and even, 0.5 natural, 0.8+ animated.
Lower --cfg slows delivery down, which pairs well with a high --exag.

Outputs Opus in an OGG container — what Telegram wants for a real voice note
(bubble + waveform) rather than a file attachment.

Long text is chunked. One generate() call stops dead at chatterbox's token cap
(~1000, around 40s of speech) and hands back the short clip with no error and
no flag — so a story returns its opening and silently loses its ending, and
nothing downstream can tell that from a genuinely short line. So: split at
sentence boundaries, generate each, stitch. Every seam lands where a reader
would breathe anyway.
"""
import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import torch
import torchaudio
from chatterbox.tts import ChatterboxTTS

p = argparse.ArgumentParser()
p.add_argument("text", help='text to speak, or "-" to read stdin')
p.add_argument("out", nargs="?", default="voice.ogg")
p.add_argument("--exag", type=float, default=0.5)
p.add_argument("--cfg", type=float, default=0.5)
p.add_argument("--audio-prompt", default=None, help="wav to clone the voice from")
p.add_argument("--cpu", action="store_true", help="force CPU (GPU is shared with Ollama)")
p.add_argument("--max-chars", type=int, default=220,
               help="chunk size — chars are a rough proxy, so stay well under the cap")
p.add_argument("--gap", type=float, default=0.35, help="pause between paragraphs (s)")
args = p.parse_args()

text = (sys.stdin.read() if args.text == "-" else args.text).strip()
if not text:
    sys.exit("nothing to speak")


def split_chunks(body, max_chars):
    """Paragraphs -> sentences -> chunks packed under max_chars.

    Sentences are never split mid-way, so a boundary always falls at a natural
    pause. Returns (text, ends_paragraph) pairs.
    """
    out = []
    for para in (x.strip() for x in re.split(r"\n\s*\n", body) if x.strip()):
        sentences = re.findall(r"[^.!?]+[.!?]+(?:\s|$)|[^.!?]+$", para.replace("\n", " "))
        packed, buf = [], ""
        for s in (s.strip() for s in sentences if s.strip()):
            if buf and len(buf) + 1 + len(s) > max_chars:
                packed.append(buf)
                buf = s
            else:
                buf = f"{buf} {s}".strip()
        if buf:
            packed.append(buf)
        for i, c in enumerate(packed):
            out.append((c, i == len(packed) - 1))
    return out


chunks = split_chunks(text, args.max_chars)

device = "cpu" if args.cpu or not torch.cuda.is_available() else "cuda"
model = ChatterboxTTS.from_pretrained(device=device)

kw = {"exaggeration": args.exag, "cfg_weight": args.cfg}
if args.audio_prompt:
    kw["audio_prompt_path"] = args.audio_prompt

pieces = []
for i, (body, ends_para) in enumerate(chunks):
    pieces.append(model.generate(body, **kw))
    if len(chunks) > 1:
        print(f"  [{i + 1}/{len(chunks)}] {body[:60]}", file=sys.stderr)
    if i < len(chunks) - 1:  # no trailing silence on the last one
        pieces.append(torch.zeros(1, int(model.sr * (args.gap if ends_para else 0.12))))

wav = torch.cat(pieces, dim=-1) if len(pieces) > 1 else pieces[0]

with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
    torchaudio.save(tmp.name, wav, model.sr)
    subprocess.run(
        ["ffmpeg", "-y", "-i", tmp.name, "-c:a", "libopus", "-b:a", "48k",
         "-ar", "48000", "-ac", "1", args.out],
        check=True, capture_output=True,
    )
    Path(tmp.name).unlink()

dur = wav.shape[-1] / model.sr
print(f"{args.out}  {dur:.1f}s  device={device}  exag={args.exag} cfg={args.cfg} "
      f"chunks={len(chunks)}")
