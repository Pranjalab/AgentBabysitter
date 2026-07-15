#!/usr/bin/env python3
"""Transcribe a Telegram voice note. Usage: transcribe.py <audio-file> [model]

Runs on CPU by default — the GPU is usually busy serving Ollama, and int8 on
28 threads clears a voice note faster than it takes to read the result.
"""
import sys
from faster_whisper import WhisperModel

path = sys.argv[1]
size = sys.argv[2] if len(sys.argv) > 2 else "small"

model = WhisperModel(size, device="cpu", compute_type="int8", cpu_threads=8)
segments, info = model.transcribe(path, beam_size=5, vad_filter=True)

print(f"[lang={info.language} p={info.language_probability:.2f} dur={info.duration:.1f}s]\n")
for s in segments:
    print(f"{s.start:6.1f}  {s.text.strip()}")
