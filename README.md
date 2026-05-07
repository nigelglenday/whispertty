# whispertty

![License](https://img.shields.io/badge/license-MIT-yellow) ![Python](https://img.shields.io/badge/python-3.11%2B-blue) ![Platform](https://img.shields.io/badge/platform-macOS-black) ![Whisper](https://img.shields.io/badge/transcription-Whisper-cyan)

> *Record. Transcribe. Browse.*

A small TUI for recording audio, transcribing with Whisper, and finding old transcripts via fuzzy search. Hit Enter on one and it opens in your Mac default app.

```
██╗    ██╗██╗  ██╗██╗███████╗██████╗ ███████╗██████╗ ████████╗████████╗██╗   ██╗
██║    ██║██║  ██║██║██╔════╝██╔══██╗██╔════╝██╔══██╗╚══██╔══╝╚══██╔══╝╚██╗ ██╔╝
██║ █╗ ██║███████║██║███████╗██████╔╝█████╗  ██████╔╝   ██║      ██║    ╚████╔╝
██║███╗██║██╔══██║██║╚════██║██╔═══╝ ██╔══╝  ██╔══██╗   ██║      ██║     ╚██╔╝
╚███╔███╔╝██║  ██║██║███████║██║     ███████╗██║  ██║   ██║      ██║      ██║
 ╚══╝╚══╝ ╚═╝  ╚═╝╚═╝╚══════╝╚═╝     ╚══════╝╚═╝  ╚═╝   ╚═╝      ╚═╝      ╚═╝
```

## What it is

Two things in one CLI:

1. A **recorder** that captures mic-only or mic + system audio, writes a WAV, runs Whisper, and saves a labeled `.txt` transcript.
2. A **picker** that lists every transcript in your transcripts directory and opens the one you select in your Mac default app.

Requires macOS, Python 3.11+, ffmpeg, openai-whisper, and (for system audio mode) BlackHole 2ch + a Multi-Output Device named "Record + Speakers".

## Install

```bash
brew install pipx ffmpeg
pipx ensurepath
pipx install git+https://github.com/nigelglenday/whispertty.git
```

You also need Whisper installed somewhere whispertty can find:

```bash
pip install -U openai-whisper
```

For local dev:

```bash
git clone https://github.com/nigelglenday/whispertty.git
cd whispertty
pipx install -e .
```

## Use

```
whispertty                       splash + arrow-key picker (type to filter)
whispertty <stem>                open that transcript directly
whispertty rec [label]           start recording (mic only)
whispertty rec --system [label]  start recording mic + system audio
whispertty stop                  stop and transcribe
whispertty status                show recording state
whispertty ls                    plain list, pipe-friendly
whispertty rm <stem>             delete transcript + audio
whispertty config show           show settings
whispertty config <key> <val>    set a setting
```

The picker also has `Record now`, `? Help`, and `Quit` entries at the bottom. While a recording is active, the picker shows `⏹ Stop recording` instead of the record entries.

## Recording

`whispertty rec [label]` records the system default mic. Set the input in System Settings → Sound → Input. One track in, one transcript out.

For meetings where you want both speakers transcribed: wear headphones and use `whispertty rec --system [label]`. This records two tracks (mic + system audio via BlackHole) and merges them with speaker labels. Without headphones the speakers play call audio into the room and the mic picks it up, producing a distorted echo loop. Headphones break the loop. This mode also requires a one-time setup:

1. `brew install blackhole-2ch` (then reboot)
2. Audio MIDI Setup → **+** → **Create Multi-Output Device**
3. Check **MacBook Air Speakers** and **BlackHole 2ch**
4. Rename the new device to **Record + Speakers**

Future: native diarization via pyannote.audio (single-track, ML-based speaker labels) is on the list.

## Config

Lives at `~/.config/whispertty/config.toml`. Created with sane defaults on first run.

```toml
[settings]
transcripts_dir = "/Users/you/Documents/transcripts"
whisper_model = "base"            # base | small | medium | large
default_recording = "mic"
auto_open_after_stop = false
keep_audio = true
output_format = "txt"
```

Edit by hand, or:

```bash
whispertty config show
whispertty config whisper_model small
whispertty config auto_open_after_stop true
```

The `transcripts_dir` defaults to `~/Documents/transcripts/`, which is also where `call-record` writes. Both tools can coexist; whispertty's picker lists everything in that dir.

## Why "whispertty"

`whisper` for the transcription engine, `tty` for the terminal interface. It records, it writes `.txt`, and it lets you browse what you've recorded without leaving the terminal.

## Limitations

- **macOS only.** AppleScript-free but uses macOS-specific tools (avfoundation, SwitchAudioSource, `open`).
- **Whisper SSL cert errors.** Some corporate VPNs (ZScaler, etc.) block Whisper's model downloads. The `base` model is bundled-ish (Whisper's first run downloads it), so on a clean machine you'll need an unrestricted network. If you hit `CERTIFICATE_VERIFY_FAILED`, run `/Applications/Python\ 3.13/Install\ Certificates.command` (adjust version), or pre-download the model on a different network and copy `~/.cache/whisper/` over.
- **No real-time transcription.** Records to file, transcribes after stop. For push-to-talk dictation, see talktype or macOS built-in dictation.

## License

MIT
