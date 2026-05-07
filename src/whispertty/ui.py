"""Themed Console, splash, and Questionary pickers for whispertty."""

import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import questionary
from rich.console import Console
from rich.theme import Theme

from .splash_art import WHISPERTTY_BANNER

# Cyan/blue audio-coded palette (intentionally distinct from a-team's orange).
_theme = Theme(
    {
        "wt": "bold cyan",
        "border": "bold blue",
        "nav": "cyan",
        "accent": "bright_cyan",
        "soft": "grey50",
    }
)
console = Console(theme=_theme)

ACTION_RECORD = {"_action": "record"}
ACTION_RECORD_SYSTEM = {"_action": "record_system"}
ACTION_HELP = {"_action": "help"}
ACTION_CANCEL = {"_action": "cancel"}

_picker_style = questionary.Style(
    [
        ("question", "bold"),
        ("pointer", "fg:#00aaff bold"),
        ("highlighted", "bold reverse"),
        ("selected", "fg:#00aaff"),
        ("answer", "fg:#00aaff bold"),
        ("instruction", "fg:#888888"),
    ]
)


def _is_tty() -> bool:
    return sys.stdout.isatty()


def print_splash(transcript_count: int) -> None:
    if not _is_tty():
        return
    console.print(f"[border]{'═' * 80}[/border]")
    console.print(f"[wt]{WHISPERTTY_BANNER}[/wt]", end="")
    suffix = f"{transcript_count} transcript{'s' if transcript_count != 1 else ''}"
    console.print(
        f"  [nav]Record. Transcribe. Browse.[/nav]   [soft]{suffix}[/soft]"
    )
    console.print(f"[border]{'═' * 80}[/border]\n")


def _human_size(n: int) -> str:
    for unit in ("B", "K", "M", "G"):
        if n < 1024:
            return f"{n:>4}{unit}"
        n //= 1024
    return f"{n}T"


def _format_row(t, label_width: int) -> str:
    label = (t.label or "(no label)")[:label_width]
    return f"  {t.timestamp}   {label:<{label_width}}   {_human_size(t.size)}"


def pick_transcript(transcripts: list, *, recording_active: bool = False) -> Optional[object]:
    """Show the picker over transcripts and bottom action items.

    Returns one of:
    - a Transcript dataclass (selected for opening)
    - ACTION_RECORD / ACTION_RECORD_SYSTEM / ACTION_HELP / ACTION_CANCEL
    - None on Ctrl-C / Esc
    """
    label_width = max((len(t.label or "(no label)") for t in transcripts), default=20)
    label_width = max(min(label_width, 40), 16)

    choices: list = []

    if transcripts:
        for t in transcripts:
            choices.append(
                questionary.Choice(title=_format_row(t, label_width), value=t.stem)
            )
        choices.append(questionary.Separator())

    if recording_active:
        choices.append(
            questionary.Choice(
                title="  ⏹  Stop recording", value={"_action": "stop"}
            )
        )
    else:
        choices.append(
            questionary.Choice(title="  ●  Record now (mic)", value=ACTION_RECORD)
        )
        choices.append(
            questionary.Choice(
                title="  ●  Record now (mic + system audio)",
                value=ACTION_RECORD_SYSTEM,
            )
        )
    choices.append(questionary.Choice(title="  ? Help", value=ACTION_HELP))
    choices.append(questionary.Choice(title="  Quit", value=ACTION_CANCEL))

    selected = questionary.select(
        "Pick a transcript",
        choices=choices,
        style=_picker_style,
        instruction="(arrow keys + type to filter)",
        use_search_filter=True,
        use_jk_keys=False,
    ).ask()

    if selected is None:
        return None
    if isinstance(selected, dict):
        return selected
    # Look up the Transcript dataclass by stem (sidesteps questionary's
    # filter-edge-case bug where dict-valued choices can return the typed
    # string).
    for t in transcripts:
        if t.stem == selected:
            return t
    return None


def prompt_label(default: str = "") -> Optional[str]:
    return questionary.text(
        "Label (optional):",
        default=default,
        style=_picker_style,
    ).ask()


def show_help(interactive: bool = True) -> None:
    if interactive:
        console.clear()

    console.print()
    console.print("[wt]WHISPERTTY HELP[/wt]")
    console.print(f"[border]{'━' * 40}[/border]")
    console.print()

    console.print("[nav]Commands[/nav]")
    console.print("  [nav]whispertty[/nav]                       splash + picker (this menu)")
    console.print("  [nav]whispertty <stem>[/nav]                open that transcript in default app")
    console.print("  [nav]whispertty rec [label][/nav]           start recording (mic by default)")
    console.print("  [nav]whispertty rec --system [label][/nav]  record mic + system audio")
    console.print("  [nav]whispertty stop[/nav]                  stop and transcribe")
    console.print("  [nav]whispertty cp <stem>[/nav]             copy transcript to clipboard")
    console.print("  [nav]whispertty status[/nav]                show recording state")
    console.print("  [nav]whispertty ls[/nav]                    plain list (pipe-friendly)")
    console.print("  [nav]whispertty rm <stem>[/nav]             delete transcript + audio")
    console.print("  [nav]whispertty config show[/nav]           show settings")
    console.print("  [nav]whispertty config <key> <val>[/nav]    set a setting")
    console.print()

    console.print("[nav]Picker keys[/nav]")
    console.print("  [nav]↑ / ↓[/nav]                            navigate")
    console.print("  [nav]type[/nav]                             fuzzy filter")
    console.print("  [nav]Enter[/nav]                            copy to clipboard")
    console.print("  [nav]Esc / Ctrl-C[/nav]                     cancel / back")
    console.print()
    console.print("[soft]To open in default app, run: whispertty <stem>[/soft]")
    console.print()

    console.print("[nav]Recording modes[/nav]")
    console.print("  [nav]mic[/nav]      single-track, system default mic. No setup.")
    console.print("  [nav]system[/nav]   two-track (mic + system audio via BlackHole),")
    console.print("           speaker labels in the merged transcript.")
    console.print("           Requires BlackHole 2ch + a Multi-Output Device named")
    console.print("           [soft]'Record + Speakers'[/soft] in Audio MIDI Setup.")
    console.print()

    console.print("[nav]Files[/nav]")
    console.print(
        f"  [soft]~/.config/whispertty/config.toml[/soft]  settings"
    )
    console.print(
        "  [soft]~/Documents/transcripts/[/soft]          transcripts (shared with call-record)"
    )
    console.print()

    console.print("[soft]https://github.com/nigelglenday/whispertty[/soft]")
    console.print()

    if interactive:
        console.print("[nav]Press Enter to return to the menu...[/nav]")
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            pass
        console.clear()


def live_recording_meter(meta) -> None:
    """Block with a live ticker until the user presses any key.

    Renders in the alternate buffer via Rich's Live (transient=True so the
    meter clears when the recording stops). Stdin is put into cbreak mode
    so a single keystroke is enough to break out — no Enter required.
    """
    import select
    import sys
    import termios
    import time
    import tty

    from rich.live import Live
    from rich.text import Text

    def _render(elapsed: float) -> Text:
        mins = int(elapsed // 60)
        secs = int(elapsed % 60)
        text = Text()
        text.append("\n  ", style="")
        text.append("●", style="bold red")
        text.append("  Recording", style="bold")
        text.append(f"  ({meta.mode})", style="soft")
        if meta.label:
            text.append(f"   {meta.label}", style="soft")
        text.append("\n\n  ", style="")
        text.append(f"{mins:02d}:{secs:02d}", style="bold wt")
        if getattr(meta, "input_device", None):
            text.append(f"   input: {meta.input_device}", style="soft")
        text.append("\n\n  ", style="")
        text.append("Press any key to stop and transcribe", style="soft")
        text.append("\n", style="")
        return text

    if not sys.stdin.isatty():
        return

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        with Live(_render(0), console=console, refresh_per_second=2, transient=True) as live:
            try:
                while True:
                    if select.select([sys.stdin], [], [], 0.4)[0]:
                        sys.stdin.read(1)
                        # Drain any remaining bytes (multi-byte keys like
                        # arrows leave trailing bytes in the stdin buffer).
                        while select.select([sys.stdin], [], [], 0)[0]:
                            sys.stdin.read(1)
                        break
                    live.update(_render(time.time() - meta.started))
            except KeyboardInterrupt:
                pass
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def info(msg: str) -> None:
    console.print(f"[nav]{msg}[/nav]")


def warn(msg: str) -> None:
    console.print(f"[accent]{msg}[/accent]")


def error(msg: str) -> None:
    console.print(f"[bold red]error:[/bold red] {msg}")
