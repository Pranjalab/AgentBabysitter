#!/usr/bin/env python3
"""Speak text as a Telegram-ready voice note.

Usage: speak.py "text" [out.ogg] [--exag 0.5] [--cfg 0.5] [--cpu]

--exag is the emotion dial: 0.3 flat and even, 0.5 natural, 0.8+ animated.
Lower --cfg slows delivery down, which pairs well with a high --exag.

Outputs Opus in an OGG container — what Telegram wants for a real voice note
(bubble + waveform) rather than a file attachment.
"""
import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

import torch
import torchaudio
from chatterbox.tts import ChatterboxTTS

p = argparse.ArgumentParser()
p.add_argument("text")
p.add_argument("out", nargs="?", default="voice.ogg")
p.add_argument("--exag", type=float, default=0.5)
p.add_argument("--cfg", type=float, default=0.5)
p.add_argument("--audio-prompt", default=None, help="wav to clone the voice from")
p.add_argument("--cpu", action="store_true", help="force CPU (GPU is shared with Ollama)")
args = p.parse_args()

device = "cpu" if args.cpu or not torch.cuda.is_available() else "cuda"
model = ChatterboxTTS.from_pretrained(device=device)

kw = {"exaggeration": args.exag, "cfg_weight": args.cfg}
if args.audio_prompt:
    kw["audio_prompt_path"] = args.audio_prompt

wav = model.generate(args.text, **kw)

with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
    torchaudio.save(tmp.name, wav, model.sr)
    subprocess.run(
        ["ffmpeg", "-y", "-i", tmp.name, "-c:a", "libopus", "-b:a", "48k",
         "-ar", "48000", "-ac", "1", args.out],
        check=True, capture_output=True,
    )
    Path(tmp.name).unlink()

dur = wav.shape[-1] / model.sr
print(f"{args.out}  {dur:.1f}s  device={device}  exag={args.exag} cfg={args.cfg}")
