#!/usr/bin/env python3
"""Speak text as a Telegram-ready voice note, using Kokoro-82M on CPU.

Usage: speak_kokoro.py "text" [out.ogg] [--voice af_heart] [--speed 1.0]
                             [--lang a] [--list-voices]
       speak_kokoro.py - out.ogg < story.txt        # "-" reads stdin

Why this exists alongside speak.py: chatterbox needs a GPU to be quick, and on
this box the GPU is often already full (llama-server, training runs), which
fails synthesis outright with a CUDA OOM. Kokoro is an 82M-parameter model that
runs comfortably on CPU, so a voice note never depends on what else holds the
card.

What it gives up: **voice cloning**. Kokoro has a fixed voice list and no
audio-prompt input. If a voice sample is configured, abs falls back to
speak.py/chatterbox, which is the only path that can clone.

Output is Opus in an OGG container — what Telegram needs for a real voice note
(bubble + waveform) rather than a file attachment. The encoder settings match
speak.py exactly so notes sound consistent whichever engine produced them.
"""
import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

# A small, deliberately curated set. Kokoro ships more, but these are the ones
# that hold up at phone-speaker quality; --list-voices prints the full set.
SUGGESTED = {
    "af_heart":   "American female, warm — the default",
    "af_bella":   "American female, brighter",
    "am_michael": "American male, even",
    "am_adam":    "American male, deeper",
    "bf_emma":    "British female",
    "bm_george":  "British male",
}

p = argparse.ArgumentParser(description="Kokoro TTS -> Telegram voice note")
p.add_argument("text", nargs="?", help='text to speak, or "-" to read stdin')
p.add_argument("out", nargs="?", default="voice.ogg", help="output .ogg path")
p.add_argument("--voice", default="af_heart", help="voice id (default af_heart)")
p.add_argument("--speed", type=float, default=1.0, help="1.0 = normal; 0.9 slower, 1.1 faster")
p.add_argument("--lang", default=None,
               help="lang code: a=American English, b=British. Default: inferred from the voice prefix.")
p.add_argument("--gap", type=float, default=0.28, help="seconds of silence between paragraphs")
p.add_argument("--max-chars", type=int, default=400, help="chunk size ceiling")
p.add_argument("--list-voices", action="store_true", help="print available voices and exit")
# Accepted and ignored so `abs say` can pass chatterbox-shaped flags through
# without the caller having to know which engine is running.
p.add_argument("--exag", type=float, default=None, help=argparse.SUPPRESS)
p.add_argument("--cfg", type=float, default=None, help=argparse.SUPPRESS)
p.add_argument("--turbo", action="store_true", help=argparse.SUPPRESS)
p.add_argument("--standard", action="store_true", help=argparse.SUPPRESS)
p.add_argument("--device", default=None, help=argparse.SUPPRESS)
p.add_argument("--cpu", action="store_true", help=argparse.SUPPRESS)
args = p.parse_args()

if args.list_voices:
    for v, d in SUGGESTED.items():
        print(f"  {v:<12} {d}")
    print("\nMore at https://huggingface.co/hexgrad/Kokoro-82M (voices/ directory).")
    sys.exit(0)

if not args.text:
    p.error("nothing to say")

text = sys.stdin.read() if args.text == "-" else args.text
text = text.strip()
if not text:
    print("Nothing to say.", file=sys.stderr)
    sys.exit(1)

# Kokoro's lang_code must match the voice or pronunciation drifts; the first
# letter of every voice id encodes it ('a' American, 'b' British, ...).
lang = args.lang or (args.voice[0] if args.voice else "a")


def split_chunks(body, max_chars):
    """Paragraphs -> sentences -> chunks packed under max_chars.

    Same shape as speak.py: sentences are never split mid-way, so every seam
    lands where a reader would breathe. Returns (text, ends_paragraph) pairs.
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

from kokoro import KPipeline  # noqa: E402  (slow import; keep it after arg parsing)

SR = 24000
pipeline = KPipeline(lang_code=lang)

pieces = []
for i, (body, ends_para) in enumerate(chunks):
    if len(chunks) > 1:
        print(f"  [{i + 1}/{len(chunks)}] {body[:60]}", file=sys.stderr)
    # KPipeline yields (graphemes, phonemes, audio) per internal segment; a
    # chunk can produce more than one, so collect them all rather than taking
    # the first and silently truncating.
    got = []
    for _gs, _ps, audio in pipeline(body, voice=args.voice, speed=args.speed):
        if audio is None:
            continue
        a = audio.detach().cpu().numpy() if hasattr(audio, "detach") else np.asarray(audio)
        got.append(a.astype(np.float32).reshape(-1))
    if not got:
        print(f"  ! chunk {i + 1} produced no audio", file=sys.stderr)
        continue
    pieces.append(np.concatenate(got))
    if i < len(chunks) - 1:  # no trailing silence on the last one
        pieces.append(np.zeros(int(SR * (args.gap if ends_para else 0.12)), dtype=np.float32))

if not pieces:
    print("Synthesis produced no audio.", file=sys.stderr)
    sys.exit(1)

wav = np.concatenate(pieces)

import soundfile as sf  # noqa: E402

with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
    sf.write(tmp.name, wav, SR)
    # loudnorm: consistent, phone-audible level across chunks and notes.
    # -application voip is Opus's speech-tuned mode. Source is 24 kHz; Opus is
    # internally 48 kHz, so keep -ar 48000 and don't push bitrate past 48k.
    # Identical to speak.py so both engines land at the same loudness.
    subprocess.run(
        ["ffmpeg", "-y", "-i", tmp.name,
         "-af", "loudnorm=I=-19:TP=-1.5:LRA=11",
         "-c:a", "libopus", "-b:a", "48k", "-vbr", "on", "-application", "voip",
         "-ar", "48000", "-ac", "1", args.out],
        check=True, capture_output=True,
    )
    Path(tmp.name).unlink()

print(f"{args.out}  {len(wav) / SR:.1f}s  device=cpu  kokoro voice={args.voice} chunks={len(chunks)}")
