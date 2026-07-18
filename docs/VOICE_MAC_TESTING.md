# Voice on a Mac — setup + testing

Goal: find the optimum voice input/output config on an Apple-Silicon Mac and
confirm the MPS (Apple GPU) path actually works. Everything here is on the
`voice-optimize` branch.

## What changed (branch `voice-optimize`)

- **`speak.py`** now selects the device `cuda → mps → cpu` (`pick_device()`), sets
  `PYTORCH_ENABLE_MPS_FALLBACK=1` so any op without an MPS kernel drops to CPU
  instead of crashing, loudness-normalises the output (consistent phone volume),
  uses Opus VoIP mode, and adds `--device` / `--turbo`. **This is the fix** that
  makes a Mac use its GPU instead of silently running on CPU for minutes.
- **`transcribe.py`** auto-sizes CPU threads, greedy-decodes (`beam_size=1`),
  pins nothing by default (auto-detects language — set `ABS_STT_LANG=en` for a
  speed win), and biases project vocabulary. CTranslate2 has no Metal backend, so
  STT stays on CPU on a Mac (fast enough for `small`).

## One-time setup on the Mac

The venvs never port across OSes — rebuild them here. Pin the same versions the
Linux box runs so behaviour matches; installing `chatterbox-tts` on macOS pulls a
**MPS-capable** torch automatically (no `+cu124` wheel).

**Versions to install**

| Piece | Version | Notes |
|---|---|---|
| Python | **3.11** | one interpreter for both venvs |
| `chatterbox-tts` | **0.1.7** | TTS; bundles the Turbo model + a macOS MPS torch |
| `faster-whisper` | **1.2.1** | STT; CTranslate2 stays CPU on Mac (no Metal) |
| ffmpeg | brew latest | Opus muxing |

```bash
brew install ffmpeg python@3.11

# TTS (chatterbox) — its own 3.11 env; the torch it pulls here is MPS-enabled
python3.11 -m venv .venv-tts
./.venv-tts/bin/pip install -U pip "chatterbox-tts==0.1.7"

# STT (faster-whisper)
python3.11 -m venv .venv
./.venv/bin/pip install -U pip "faster-whisper==1.2.1"
```

## Paste-ready prompt for Claude on the Mac

Open Claude Code in the AgentBabysitter repo on the Mac and paste this:

> You're on my Apple-Silicon Mac, in the AgentBabysitter repo. Set up and
> benchmark the voice pipeline on the `voice-optimize` branch and report real
> numbers. Steps:
>
> 1. `git fetch && git checkout voice-optimize`
> 2. Install prereqs if missing: `brew install ffmpeg python@3.11`
> 3. Build both venvs (they don't port across machines):
>    - `python3.11 -m venv .venv-tts && ./.venv-tts/bin/pip install -U pip "chatterbox-tts==0.1.7"`
>    - `python3.11 -m venv .venv && ./.venv/bin/pip install -U pip "faster-whisper==1.2.1"`
> 4. `chmod +x voicelab.sh && ./voicelab.sh` then `./voicelab.sh --turbo` (turbo downloads its weights once).
> 5. Read `docs/VOICE_PIPELINE_ANALYSIS.md` and `docs/VOICE_MAC_TESTING.md` for what's being measured.
>
> Then report back:
> - Does MPS (Apple GPU) complete a TTS synthesis? Wall time + RTF vs CPU.
> - Does `--turbo` work, and how much faster than standard on MPS?
> - Any op with NO MPS kernel (voicelab prints it) — name it.
> - STT: is `small` accurate/fast enough on CPU, or is `large-v3-turbo` worth it?
> - Your recommended Mac defaults (device, model, turbo yes/no), and paste the full `voicelab.sh` output.
>
> If MPS crashes on an op even with `PYTORCH_ENABLE_MPS_FALLBACK=1` (speak.py sets
> it), fall back to `--device cpu`, name the failing op, and DON'T change any
> committed code — just report the correct fix so we decide together.

## Run the benchmark

```bash
git fetch && git checkout voice-optimize
chmod +x voicelab.sh
./voicelab.sh                 # STT + TTS across mps + cpu, round-trip accuracy
./voicelab.sh --turbo         # also test ChatterboxTurboTTS (2-4x faster; one-time download)
./voicelab.sh --audio a-real-note.oga   # also transcribe a genuine voice note
```

It prints, per device: whether it ran, wall time, real-time factor, and — for
STT — the transcript and a word-match score. On any failure it prints the actual
error and a suggested fix (MPS op gaps, OOM, missing packages). **Send the whole
output back** and I'll set the Mac defaults from real numbers.

## What we're trying to learn

1. Does **MPS** complete a synthesis, and how much faster than CPU? (Expect a
   large gap — CPU is minutes, MPS should be seconds-ish.)
2. Any op that has **no MPS kernel** — the harness names it; that tells us whether
   to keep the CPU fallback or file it upstream.
3. Is **`--turbo`** worth defaulting to on Mac (speed vs the lost emotion dials)?
4. STT: is `small` enough, or is `large-v3-turbo` / `distil-large-v3.5` worth the
   extra CPU time? If STT is too slow on Mac, the next step is `mlx-whisper`
   (Metal) behind the same CLI — not faster-whisper.

## Manual spot-check (optional)

```bash
# TTS on the Apple GPU, keep the file:
./.venv-tts/bin/python speak.py "Testing the Apple GPU path." out.ogg --device mps
# force CPU to compare:
./.venv-tts/bin/python speak.py "Testing CPU." cpu.ogg --device cpu
# transcribe it back:
./.venv/bin/python transcribe.py out.ogg
```
