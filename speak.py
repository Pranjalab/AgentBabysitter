#!/usr/bin/env python3
"""Speak text as a Telegram-ready voice note.

Usage: speak.py "text" [out.ogg] [--exag 0.5] [--cfg 0.5]
                       [--device auto|cuda|mps|cpu] [--cpu] [--turbo]
       speak.py - out.ogg < story.txt        # "-" reads stdin

--exag is the emotion dial: 0.3 flat and even, 0.5 natural, 0.8+ animated.
Lower --cfg slows delivery down, which pairs well with a high --exag.
(Both are ignored under --turbo, which has no CFG/emotion dial.)

--device picks the accelerator: auto = CUDA if present, else CPU. On an
Apple-Silicon Mac auto stays on CPU on purpose: chatterbox TTS benchmarks
~1.6-1.9x SLOWER on MPS than CPU here, so MPS is opt-in via --device mps rather
than the default. --cpu forces CPU. --turbo uses ChatterboxTurboTTS (no CFG, a
2-step distilled decoder; downloads its weights once).

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
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

# Let unimplemented MPS ops silently fall back to CPU instead of crashing. Must
# be set before torch initialises, so it lives above the torch import.
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

import torch
import torchaudio

p = argparse.ArgumentParser()
p.add_argument("text", help='text to speak, or "-" to read stdin')
p.add_argument("out", nargs="?", default="voice.ogg")
p.add_argument("--exag", type=float, default=0.5)
p.add_argument("--cfg", type=float, default=0.5)
p.add_argument("--audio-prompt", default=None, help="wav to clone the voice from")
p.add_argument("--device", default="auto", choices=["auto", "cuda", "mps", "cpu"],
               help="accelerator (default auto: cuda else cpu; MPS is slower for this model, use --device mps to force)")
p.add_argument("--cpu", action="store_true", help="force CPU (shortcut for --device cpu)")
p.add_argument("--turbo", action="store_true",
               help="ChatterboxTurboTTS: no CFG, 2-step decoder, ~1.8x faster generation on GPU")
p.add_argument("--max-chars", type=int, default=300,
               help="chunk size — chars are a rough proxy, so stay well under the cap")
p.add_argument("--gap", type=float, default=0.35, help="pause between paragraphs (s)")
args = p.parse_args()

text = (sys.stdin.read() if args.text == "-" else args.text).strip()
if not text:
    sys.exit("nothing to speak")


def pick_device(pref, force_cpu):
    """Explicit choice wins. In auto mode: CUDA if present, else CPU.

    We deliberately do NOT auto-select Apple MPS. Measured on an M-series Mac,
    chatterbox TTS runs ~1.6-1.9x SLOWER on MPS than on CPU (the T3 stage is a
    small-batch autoregressive loop, and MPS-fallback for the unimplemented ops
    adds host<->device copies that outweigh any GPU win). Auto would otherwise
    pick the slower path on every Mac. MPS is still reachable with an explicit
    --device mps for anyone who wants to measure it themselves.
    """
    if force_cpu:
        return "cpu"
    if pref != "auto":
        return pref
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


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
device = pick_device(args.device, args.cpu)

# Load the model and bind a generate() that only passes kwargs the path supports.
if args.turbo:
    from chatterbox.tts_turbo import ChatterboxTurboTTS
    model = ChatterboxTurboTTS.from_pretrained(device=device)

    def generate(body):
        kw = {}
        if args.audio_prompt:          # turbo needs a >5s reference clip
            kw["audio_prompt_path"] = args.audio_prompt
        return model.generate(body, **kw)
else:
    from chatterbox.tts import ChatterboxTTS
    model = ChatterboxTTS.from_pretrained(device=device)
    base_kw = {"exaggeration": args.exag, "cfg_weight": args.cfg}
    if args.audio_prompt:
        base_kw["audio_prompt_path"] = args.audio_prompt

    def generate(body):
        return model.generate(body, **base_kw)

pieces = []
for i, (body, ends_para) in enumerate(chunks):
    pieces.append(generate(body))
    if len(chunks) > 1:
        print(f"  [{i + 1}/{len(chunks)}] {body[:60]}", file=sys.stderr)
    if i < len(chunks) - 1:  # no trailing silence on the last one
        pieces.append(torch.zeros(1, int(model.sr * (args.gap if ends_para else 0.12))))

wav = torch.cat(pieces, dim=-1) if len(pieces) > 1 else pieces[0]

with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
    torchaudio.save(tmp.name, wav.cpu(), model.sr)
    # loudnorm: consistent, phone-audible level across chunks and notes.
    # -application voip: Opus's speech-tuned mode. Source is 24 kHz; Opus is
    # internally 48 kHz, so keep -ar 48000 and don't push bitrate past 48k.
    subprocess.run(
        ["ffmpeg", "-y", "-i", tmp.name,
         "-af", "loudnorm=I=-19:TP=-1.5:LRA=11",
         "-c:a", "libopus", "-b:a", "48k", "-vbr", "on", "-application", "voip",
         "-ar", "48000", "-ac", "1", args.out],
        check=True, capture_output=True,
    )
    Path(tmp.name).unlink()

dur = wav.shape[-1] / model.sr
mode = "turbo" if args.turbo else f"exag={args.exag} cfg={args.cfg}"
print(f"{args.out}  {dur:.1f}s  device={device}  {mode}  chunks={len(chunks)}")
