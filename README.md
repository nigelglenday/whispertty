# whispertty

![License](https://img.shields.io/badge/license-MIT-yellow) ![Python](https://img.shields.io/badge/python-3.11%2B-blue) ![Platform](https://img.shields.io/badge/platform-macOS-black) ![Whisper](https://img.shields.io/badge/transcription-Whisper-cyan)

Record audio, transcribe with Whisper, optionally label speakers with pyannote, browse from a TUI picker.

```
██╗    ██╗██╗  ██╗██╗███████╗██████╗ ███████╗██████╗ ████████╗████████╗██╗   ██╗
██║    ██║██║  ██║██║██╔════╝██╔══██╗██╔════╝██╔══██╗╚══██╔══╝╚══██╔══╝╚██╗ ██╔╝
██║ █╗ ██║███████║██║███████╗██████╔╝█████╗  ██████╔╝   ██║      ██║    ╚████╔╝
██║███╗██║██╔══██║██║╚════██║██╔═══╝ ██╔══╝  ██╔══██╗   ██║      ██║     ╚██╔╝
╚███╔███╔╝██║  ██║██║███████║██║     ███████╗██║  ██║   ██║      ██║      ██║
 ╚══╝╚══╝ ╚═╝  ╚═╝╚═╝╚══════╝╚═╝     ╚══════╝╚═╝  ╚═╝   ╚═╝      ╚═╝      ╚═╝
```

## Requirements

- macOS, Python 3.11+
- ffmpeg
- openai-whisper or mlx-whisper
- (optional, for diarization) pyannote.audio + Hugging Face token
- (optional, for system audio capture) BlackHole 2ch + Multi-Output Device

## Install

```bash
brew install pipx ffmpeg
pipx ensurepath
pipx install git+https://github.com/nigelglenday/whispertty.git
pip install -U openai-whisper

# optional: faster transcription on Apple Silicon
pipx inject whispertty mlx-whisper

# optional: speaker diarization
pipx inject whispertty pyannote.audio
```

## Commands

```
whispertty                       splash + arrow-key picker (type to filter)
whispertty <stem>                open transcript directly
whispertty rec [label]           start recording, backgrounds
whispertty rec --system [label]  record mic + system audio (requires BlackHole)
whispertty stop                  stop and transcribe
whispertty diarize <stem>        label speakers in an existing recording
whispertty cp <stem>             copy transcript to clipboard
whispertty status                recording state
whispertty ls                    plain list, pipe-friendly
whispertty rm <stem>             delete transcript and audio
whispertty config show           show settings
whispertty config <key> <val>    set a setting
whispertty help                  styled help
```

In the picker, Enter on a transcript opens a preview with actions: Copy / Open in default app / Reveal in Finder / Diarize / Delete / Back.

## Recording

`whispertty rec` records the system default mic. Set the input in System Settings → Sound → Input.

`whispertty rec --system` records two tracks (mic + system audio via BlackHole) and merges them with `Remote:` / `Local:` labels. Setup:

1. `brew install blackhole-2ch`, then reboot
2. Audio MIDI Setup → **+** → Create Multi-Output Device
3. Check your normal output (e.g. MacBook Air Speakers) and BlackHole 2ch
4. Rename the device to **Record + Speakers**

System mode auto-switches the system output when you start, restores it on stop. Wear headphones during the recording or your mic will pick up the speaker output and produce a distorted echo.

## Diarization

Single-track recordings can be retroactively labeled by speaker:

```bash
whispertty diarize <stem>
```

Replaces the plain transcript with `[mm:ss] SPEAKER_00:` / `SPEAKER_01:` labels. One-time setup:

1. Free Hugging Face account at [huggingface.co](https://huggingface.co)
2. Accept terms at:
   - [pyannote/speaker-diarization-3.1](https://huggingface.co/pyannote/speaker-diarization-3.1)
   - [pyannote/segmentation-3.0](https://huggingface.co/pyannote/segmentation-3.0)
   - [pyannote/speaker-diarization-community-1](https://huggingface.co/pyannote/speaker-diarization-community-1)
3. Generate a read token at [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens)
4. `whispertty config hf_token hf_xxxxxxxx`

Diarization is also available as an action in the picker preview.

## Backends

`whisper_backend` config controls which Whisper engine runs:

- `auto` (default): use mlx-whisper if installed, else openai-whisper
- `mlx`: force mlx-whisper (Apple Silicon, ~4x faster)
- `openai`: force openai-whisper

Models: `tiny`, `base`, `small`, `medium`, `large`. Set with `whispertty config whisper_model small`.

## Config

`~/.config/whispertty/config.toml`:

```toml
[settings]
transcripts_dir = "/Users/you/Documents/transcripts"
whisper_model = "base"
whisper_backend = "auto"
default_recording = "mic"
auto_open_after_stop = false
auto_copy_on_stop = true
keep_audio = true
output_format = "txt"
hf_token = ""
```

## Files

- `~/.config/whispertty/config.toml` — settings
- `~/Documents/transcripts/` — `.wav` (audio) + `.json` (Whisper segments) + `.txt` (transcript)
- `~/.whispertty.pid` / `~/.whispertty.meta` — recording state

## License

MIT
