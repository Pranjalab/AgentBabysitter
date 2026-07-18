#!/usr/bin/env python3
"""Transcribe a Telegram voice note. Usage: transcribe.py <audio-file> [model]

CPU by default — the GPU is usually busy serving Ollama, and int8 on half the
cores clears a short voice note in a second or two. Set ABS_STT_GPU=1 to run on
CUDA when it's free (near-instant, even for the large models).

Tuning via env (all optional, so the CLI contract stays `transcribe.py <file> [model]`):
  ABS_STT_GPU=1         use CUDA if CTranslate2 sees a device (else CPU)
  ABS_STT_THREADS=N     CPU threads (default: half the cores)
  ABS_STT_LANG=en       pin the language (skips the detect pass, faster + no
                        misdetection on short clips); unset/"auto" = auto-detect
  ABS_STT_PROMPT="..."  initial prompt to bias jargon and project names

Apple Silicon note: CTranslate2 has no Metal/MPS backend, so on a Mac this always
runs on CPU (fast enough for `small`). For Metal-accelerated STT on a Mac, drop in
mlx-whisper or whisper.cpp behind this same CLI — see docs/VOICE_MAC_TESTING.md.
"""
import os
import sys
from faster_whisper import WhisperModel

path = sys.argv[1]
size = sys.argv[2] if len(sys.argv) > 2 else "small"

# Device: CPU unless ABS_STT_GPU is set AND CTranslate2 actually sees a GPU.
# (CTranslate2 is CUDA/CPU only — there is no MPS path to probe.)
use_gpu = False
if os.environ.get("ABS_STT_GPU"):
    try:
        import ctranslate2
        use_gpu = ctranslate2.get_cuda_device_count() > 0
    except Exception:
        use_gpu = False
device = "cuda" if use_gpu else "cpu"
compute_type = "int8_float16" if use_gpu else "int8"

# Threads: int8 GEMMs scale usefully to about half the cores before they hit
# memory bandwidth. Half the machine's cores is a good default everywhere.
default_threads = max(4, (os.cpu_count() or 8) // 2)
cpu_threads = int(os.environ.get("ABS_STT_THREADS") or default_threads)

# Language: auto-detect by default (safe for mixed-language senders). Pin with
# ABS_STT_LANG=en for a speed win if you only ever send English.
lang = os.environ.get("ABS_STT_LANG", "").strip().lower()
language = None if lang in ("", "auto") else lang

# initial_prompt biases decoding toward these tokens — the cheapest fix for
# project names and jargon getting mangled.
prompt = os.environ.get(
    "ABS_STT_PROMPT",
    "Claude, Claude Code, Anthropic, venv, ffmpeg, Telegram, abs, Ollama, git, Pranjal",
)

model = WhisperModel(size, device=device, compute_type=compute_type, cpu_threads=cpu_threads)
# beam_size=1 (greedy) is ~2-3x faster than beam search on CPU at a negligible
# accuracy cost for clean, short, single-speaker notes; condition_on_previous_text
# off avoids the repetition-loop hallucinations and shaves a little more time.
segments, info = model.transcribe(
    path,
    beam_size=1,
    vad_filter=True,
    language=language,
    condition_on_previous_text=False,
    initial_prompt=prompt,
)

print(f"[lang={info.language} p={info.language_probability:.2f} dur={info.duration:.1f}s "
      f"model={size} device={device} threads={cpu_threads}]\n")
for s in segments:
    print(f"{s.start:6.1f}  {s.text.strip()}")
