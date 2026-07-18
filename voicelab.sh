#!/usr/bin/env bash
# voicelab.sh — benchmark the abs voice pipeline (speak.py / transcribe.py) on
# THIS machine and print the optimum config. Built to find the right Apple-Silicon
# (MPS) vs CPU settings on a Mac, and to surface the *actual* fixes when a device
# path breaks.
#
# Safe: it does NOT modify the repo, the venvs, or any config. It only runs the
# two CLI scripts and times them, writing samples to a temp dir (removed unless
# --keep). Read-only diagnostics.
#
# Usage:
#   ./voicelab.sh                       # STT + TTS across this platform's devices
#   ./voicelab.sh --turbo               # also test ChatterboxTurboTTS (downloads once)
#   ./voicelab.sh --devices "mps cpu"   # override the TTS device list
#   ./voicelab.sh --stt-models "small"  # limit STT models (default: small large-v3-turbo)
#   ./voicelab.sh --text "a sentence"   # custom sentence to speak
#   ./voicelab.sh --audio note.oga      # also transcribe a real voice note
#   ./voicelab.sh --keep out            # keep generated samples in ./out
#
# Send the whole output back — it contains everything needed to lock in the Mac config.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PY_TTS="$HERE/.venv-tts/bin/python"
PY_STT="$HERE/.venv/bin/python"
SPEAK="$HERE/speak.py"
TRANSCRIBE="$HERE/transcribe.py"

TEXT="Agent Babysitter voice check. The quick brown fox jumps over the lazy dog, then commits to git."
DEVICES=""
STT_MODELS="small large-v3-turbo"
TURBO=0
KEEP=""
AUDIO=""

while [ $# -gt 0 ]; do
  case "$1" in
    --devices)    DEVICES="$2"; shift 2 ;;
    --stt-models) STT_MODELS="$2"; shift 2 ;;
    --text)       TEXT="$2"; shift 2 ;;
    --audio)      AUDIO="$2"; shift 2 ;;
    --keep)       KEEP="$2"; shift 2 ;;
    --turbo)      TURBO=1; shift ;;
    -h|--help)    sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

# --- colours (skip if not a tty) ---
if [ -t 1 ]; then B=$'\033[1m'; D=$'\033[2m'; G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; X=$'\033[0m'
else B=; D=; G=; Y=; R=; X=; fi
say()  { printf '%s\n' "$*"; }
head() { printf '\n%s== %s ==%s\n' "$B" "$*" "$X"; }

# --- workspace ---
if [ -n "$KEEP" ]; then WORK="$KEEP"; mkdir -p "$WORK"; else WORK="$(mktemp -d 2>/dev/null || mktemp -d -t voicelab)"; fi
cleanup() { [ -z "$KEEP" ] && rm -rf "$WORK"; }
trap cleanup EXIT

OS="$(uname -s)"; ARCH="$(uname -m)"

# Portable high-res clock via python3 (present on macOS and Linux).
_now() { python3 -c 'import time;print("%.3f"%time.time())' 2>/dev/null || date +%s; }

# run <logfile> <cmd...> -> prints elapsed seconds to stdout, rc via return.
# On failure the caller inspects the logfile for the real error.
run() {
  local log="$1"; shift
  local t0 t1
  t0="$(_now)"
  "$@" >"$log" 2>&1
  local rc=$?
  t1="$(_now)"
  python3 -c "print('%.1f' % ($t1 - $t0))" 2>/dev/null || echo "?"
  return $rc
}

# --- preflight ---
head "Environment"
say "OS: $OS   arch: $ARCH   host bash: ${BASH_VERSION:-?}"
missing=0
[ -x "$PY_TTS" ] || { say "${R}✗ TTS venv missing:${X} $PY_TTS"; missing=1; }
[ -x "$PY_STT" ] || { say "${R}✗ STT venv missing:${X} $PY_STT"; missing=1; }
command -v ffmpeg >/dev/null 2>&1 || { say "${R}✗ ffmpeg not on PATH${X}"; missing=1; }
if [ "$missing" = 1 ]; then
  cat <<EOF

${Y}Set up the voice venvs on this machine first (they never port across OSes):${X}
  python3.11 -m venv .venv-tts && ./.venv-tts/bin/pip install chatterbox-tts
  python3    -m venv .venv     && ./.venv/bin/pip install faster-whisper
  # macOS: brew install ffmpeg
Then re-run ./voicelab.sh. See docs/VOICE_MAC_TESTING.md.
EOF
  exit 1
fi

# Probe torch / device availability from inside the TTS venv.
say ""
"$PY_TTS" - <<'PY'
import torch
mps = getattr(torch.backends, "mps", None)
mps_ok = bool(mps and mps.is_available())
print(f"torch {torch.__version__}   cuda={torch.cuda.is_available()}   mps={mps_ok}")
if not torch.cuda.is_available() and not mps_ok:
    print("  -> no accelerator: TTS will run on CPU (slow — minutes per paragraph)")
PY
# Probe STT backend.
"$PY_STT" - <<'PY'
import faster_whisper, ctranslate2, os
n = ctranslate2.get_cuda_device_count()
print(f"faster-whisper {faster_whisper.__version__}  ctranslate2 {ctranslate2.__version__}  "
      f"cuda_devices={n}  cores={os.cpu_count()}")
print("  (CTranslate2 has no Metal backend — STT runs on CPU on a Mac, by design)")
PY

# Decide which TTS devices to try, by platform, unless overridden.
if [ -z "$DEVICES" ]; then
  case "$OS" in
    Darwin) DEVICES="mps cpu" ;;
    *)      DEVICES="auto cpu" ;;   # auto -> cuda where present
  esac
fi

# ============================ TTS BENCHMARK ============================
head "TTS (text -> voice note)  —  $(echo "$TEXT" | cut -c1-60)..."
BEST_TTS_DEV=""; BEST_TTS_T=""; TTS_OK=""
tts_variants="$DEVICES"

run_tts() {  # <label> <device> <extra speak.py args...>
  local label="$1" dev="$2"; shift 2
  local out="$WORK/tts_${label}.ogg" log="$WORK/tts_${label}.log" secs
  printf '  %-18s ' "$label"
  secs="$(run "$log" "$PY_TTS" "$SPEAK" "$TEXT" "$out" --device "$dev" "$@")"
  if [ $? -eq 0 ] && [ -s "$out" ]; then
    local audio rtf=""
    audio="$(awk 'match($0,/[0-9]+\.[0-9]+s +device/){s=substr($0,RSTART,RLENGTH); sub(/s.*/,"",s); print s; exit}' "$log")"
    [ -n "$audio" ] && rtf="$(python3 -c "print('  RTF %.2fx'%($secs/$audio))" 2>/dev/null)"
    say "${G}ok${X}  ${secs}s wall${rtf:+ ·$rtf}  -> $(basename "$out")"
    TTS_OK="$TTS_OK $label"
    if [ -z "$BEST_TTS_T" ] || python3 -c "import sys;sys.exit(0 if $secs<$BEST_TTS_T else 1)"; then
      BEST_TTS_T="$secs"; BEST_TTS_DEV="$label"
    fi
    LAST_TTS_OGG="$out"
  else
    say "${R}FAILED${X} (${secs}s) — likely fix below"
    printf '      %s\n' "$(tail -n 3 "$log" | tr '\n' ' ' | cut -c1-200)"
    # Heuristic fixes for the common Mac failure modes.
    case "$(tr 'A-Z' 'a-z' <"$log")" in
      *"mps"*"not"*implement*|*"could not run"*mps*)
        say "      ${Y}fix:${X} an op has no MPS kernel. PYTORCH_ENABLE_MPS_FALLBACK=1 is set by speak.py; if it still crashes, use --device cpu for now and report this op." ;;
      *"out of memory"*|*"mps backend out of memory"*)
        say "      ${Y}fix:${X} MPS OOM — close other GPU apps, or shorten --max-chars." ;;
      *"no module named 'chatterbox'"*)
        say "      ${Y}fix:${X} .venv-tts is missing chatterbox-tts — reinstall it." ;;
    esac
  fi
}

for dev in $tts_variants; do
  run_tts "$dev" "$dev"
done
if [ "$TURBO" = 1 ]; then
  for dev in $tts_variants; do
    run_tts "turbo-$dev" "$dev" --turbo
  done
fi

# ============================ STT BENCHMARK ============================
# Transcribe the best TTS sample (round-trip: known text in, text out) and, if
# given, a real voice note. Round-trip doubles as an accuracy check.
head "STT (voice note -> text)"
STT_SRC="${LAST_TTS_OGG:-}"
[ -n "$AUDIO" ] && [ -f "$AUDIO" ] && STT_SRC="$AUDIO"
if [ -z "$STT_SRC" ] || [ ! -s "$STT_SRC" ]; then
  say "${Y}no audio to transcribe (TTS produced nothing). Pass --audio note.oga to test STT alone.${X}"
else
  say "source: $(basename "$STT_SRC")"
  BEST_STT_MODEL=""; BEST_STT_SCORE=-1
  for m in $STT_MODELS; do
    log="$WORK/stt_${m}.log"
    printf '  %-18s ' "$m"
    secs="$(run "$log" "$PY_STT" "$TRANSCRIBE" "$STT_SRC" "$m")"
    if [ $? -eq 0 ]; then
      # collapse the transcript (drop the [lang=...] header + timestamps)
      got="$(grep -vE '^\[lang=' "$log" | sed -E 's/^[[:space:]]*[0-9.]+[[:space:]]+//' | tr '\n' ' ' | sed -E 's/  +/ /g; s/^ //; s/ $//')"
      # word-overlap score vs the known text (only meaningful for the round-trip)
      score=""
      if [ -z "$AUDIO" ]; then
        score="$(python3 - "$TEXT" "$got" <<'PY'
import sys, re
norm=lambda s:set(re.findall(r"[a-z0-9]+", s.lower()))
a,b=norm(sys.argv[1]),norm(sys.argv[2])
print(f"{100*len(a&b)//max(1,len(a))}")
PY
)"
      fi
      say "${G}ok${X}  ${secs}s${score:+  words matched: ${score}%}"
      printf '      %s"%s"\n' "$D" "$(echo "$got" | cut -c1-140)$X"
      if [ -n "$score" ] && [ "$score" -gt "$BEST_STT_SCORE" ]; then
        BEST_STT_SCORE="$score"; BEST_STT_MODEL="$m"
      fi
    else
      say "${R}FAILED${X} (${secs}s)"
      printf '      %s\n' "$(tail -n 3 "$log" | tr '\n' ' ' | cut -c1-200)"
      case "$(tr 'A-Z' 'a-z' <"$log")" in
        *"out of memory"*|*"cannot reserve"*)
          say "      ${Y}fix:${X} $m is too big for this box on CPU — use small or distil-large-v3.5." ;;
        *"no such file"*|*"invalid"*|*"failed to load audio"*)
          say "      ${Y}fix:${X} ffmpeg couldn't read the audio — check the file / ffmpeg install." ;;
      esac
    fi
  done
fi

# ============================ VERDICT ============================
head "Verdict for this machine ($OS/$ARCH)"
if [ -n "$BEST_TTS_DEV" ]; then
  say "${G}Fastest TTS:${X} --device ${BEST_TTS_DEV%%-*}  (${BEST_TTS_T}s wall for the test sentence)"
  case " $TTS_OK " in
    *" mps "*) say "  ✓ MPS works — the Apple GPU path is live. This is the Mac default speak.py now picks." ;;
    *) [ "$OS" = Darwin ] && say "  ${Y}MPS did not complete — see the fix above; CPU is the fallback.${X}" ;;
  esac
else
  say "${R}No TTS device completed — see failures above.${X}"
fi
if [ -n "${BEST_STT_MODEL:-}" ]; then
  say "${G}Best STT (round-trip accuracy):${X} $BEST_STT_MODEL at ${BEST_STT_SCORE}% words matched"
  say "  On a Mac all STT is CPU. If it feels slow, try model 'distil-large-v3.5', or mlx-whisper for Metal."
fi
say ""
say "${B}Send this whole output back${X} and I'll lock the Mac defaults in. Samples: ${KEEP:-(temp, discarded)}"
