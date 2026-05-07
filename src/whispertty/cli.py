"""whispertty — main CLI entry point.

Subcommand layout:
    whispertty                  picker over transcripts (default)
    whispertty <stem>           direct-open shortcut for any transcript stem
    whispertty rec [label]      start a mic recording (backgrounds)
    whispertty rec --system     start mic + system-audio recording
    whispertty stop             stop and transcribe
    whispertty status           recording state
    whispertty ls               plain list
    whispertty rm <stem>        delete transcript + sibling files
    whispertty config show      show settings
    whispertty config <k> <v>   set a setting
    whispertty help             styled help panel
"""

import subprocess
import sys
import time
from pathlib import Path

import click

from . import config, recorder, transcribe, transcripts, ui


class WhisperGroup(click.Group):
    """Routes unknown commands to the direct-open shortcut, so
    `whispertty 2026-05-07_08-24_livecall` opens that transcript without
    going through the picker."""

    def get_command(self, ctx, cmd_name):
        rv = super().get_command(ctx, cmd_name)
        if rv is not None:
            return rv

        @click.command(name=cmd_name, help=f"Open transcript '{cmd_name}'.")
        def shortcut():
            t = transcripts.find(cmd_name)
            if not t:
                ui.error(f"transcript not found: {cmd_name}")
                ui.console.print(
                    "[soft]Run `whispertty ls` to see existing transcripts.[/soft]"
                )
                sys.exit(1)
            _open_in_default_app(t.path)

        return shortcut


@click.group(
    cls=WhisperGroup,
    invoke_without_command=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.option("--no-splash", is_flag=True, help="Suppress the splash banner.")
@click.pass_context
def cli(ctx: click.Context, no_splash: bool) -> None:
    """Record, transcribe with Whisper, browse from a TUI."""
    ctx.ensure_object(dict)
    ctx.obj["no_splash"] = no_splash
    if ctx.invoked_subcommand is None:
        run_picker(no_splash=no_splash)


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


@cli.command("rec")
@click.argument("label", required=False, default=None)
@click.option(
    "--system",
    "system_mode",
    is_flag=True,
    help="Record mic + system audio via BlackHole (requires 'Record + Speakers' Multi-Output Device).",
)
def rec_cmd(label: str | None, system_mode: bool) -> None:
    """Start a backgrounded recording. Use `whispertty stop` to end."""
    mode = "system" if system_mode else "mic"
    try:
        meta = recorder.start(
            transcripts_dir=config.transcripts_dir(),
            mode=mode,
            label=label,
        )
    except (RuntimeError, ValueError) as e:
        ui.error(str(e))
        sys.exit(1)

    ui.info(f"Recording ({meta.mode}) started [PID {meta.pid}]")
    if meta.prev_output and meta.mode == "system":
        ui.console.print(
            f"[soft]Audio output switched: '{meta.prev_output}' → '{recorder.MULTI_OUTPUT_NAME}'[/soft]"
        )
    for f in meta.files:
        ui.console.print(f"[soft]  {f}[/soft]")
    ui.console.print("\n[nav]Run `whispertty stop` when done.[/nav]")


@cli.command("stop")
def stop_cmd() -> None:
    """Stop the current recording and transcribe."""
    if not recorder.is_recording():
        meta = recorder.read_meta()
        if meta:
            recorder.cleanup()
        ui.error("No active recording.")
        sys.exit(1)

    try:
        meta = recorder.stop()
    except RuntimeError as e:
        ui.error(str(e))
        sys.exit(1)

    duration_min = max(0, int((time.time() - meta.started) / 60))
    ui.info(f"Recording stopped ({duration_min}m). Transcribing...")

    final_txt = _transcribe_and_finalize(meta)
    recorder.cleanup()

    if final_txt:
        ui.info(f"Saved: {final_txt}")
        if config.get("auto_open_after_stop"):
            _open_in_default_app(final_txt)
    else:
        ui.error("Transcription produced no output. Audio files kept for inspection.")


@cli.command("status")
def status_cmd() -> None:
    """Show whether a recording is in progress."""
    meta = recorder.read_meta()
    if not meta or not recorder.is_recording():
        ui.console.print("[soft]No active recording.[/soft]")
        if meta and not recorder.is_recording():
            ui.console.print("[soft]Stale state files cleaned up.[/soft]")
            recorder.cleanup()
        return
    elapsed = int(time.time() - meta.started)
    mins, secs = elapsed // 60, elapsed % 60
    ui.console.print(f"[wt]Recording active[/wt] (PID {meta.pid})")
    ui.console.print(f"  mode:     [nav]{meta.mode}[/nav]")
    if meta.label:
        ui.console.print(f"  label:    [nav]{meta.label}[/nav]")
    ui.console.print(f"  duration: [nav]{mins}m {secs}s[/nav]")
    for f in meta.files:
        ui.console.print(f"  file:     [soft]{f}[/soft]")


@cli.command("ls")
def ls_cmd() -> None:
    """List transcripts in plain text (pipe-friendly).

    Output: stem <TAB> label <TAB> size_bytes <TAB> path
    """
    items = transcripts.list_transcripts()
    if not items:
        return
    for t in items:
        label = t.label or ""
        print(f"{t.stem}\t{label}\t{t.size}\t{t.path}")


@cli.command("rm")
@click.argument("stem")
def rm_cmd(stem: str) -> None:
    """Delete a transcript and any sibling audio/json files."""
    if stem.endswith(".txt"):
        stem = stem[:-4]
    removed = transcripts.delete(stem)
    if not removed:
        ui.error(f"no files found matching: {stem}")
        sys.exit(1)
    ui.info(f"Removed {len(removed)} file(s):")
    for p in removed:
        ui.console.print(f"  [soft]{p}[/soft]")


@cli.command("help")
def help_cmd() -> None:
    """Show the styled help panel (same as `? Help` in the picker)."""
    ui.show_help(interactive=False)


# ---------------------------------------------------------------------------
# `whispertty config` group
# ---------------------------------------------------------------------------


@cli.group("config")
def config_grp() -> None:
    """Show or change settings."""


@config_grp.command("show")
def config_show() -> None:
    settings = config.load_settings()
    width = max(len(k) for k in settings)
    for k, v in settings.items():
        print(f"{k:<{width}}  {v}")


@config_grp.command("set", hidden=True)
@click.argument("key")
@click.argument("value")
def config_set(key: str, value: str) -> None:
    if key not in config.DEFAULTS:
        ui.error(
            f"unknown setting: {key}. Valid keys: {', '.join(config.DEFAULTS)}"
        )
        sys.exit(1)
    config.set_value(key, value)
    ui.info(f"Set {key} → {config.get(key)}")


class _ConfigGroup(click.Group):
    """Routes `whispertty config <key> <value>` (no explicit `set` verb) to a
    synthetic setter command, so users can write `config whisper_model small`
    instead of `config set whisper_model small`."""

    def get_command(self, ctx, cmd_name):
        rv = super().get_command(ctx, cmd_name)
        if rv is not None:
            return rv
        if cmd_name in config.DEFAULTS:
            @click.command(name=cmd_name, help=f"Set {cmd_name}.")
            @click.argument("value")
            def setter(value):
                config.set_value(cmd_name, value)
                ui.info(f"Set {cmd_name} → {config.get(cmd_name)}")
            return setter
        return None


config_grp.__class__ = _ConfigGroup


# ---------------------------------------------------------------------------
# Default picker mode
# ---------------------------------------------------------------------------


def run_picker(no_splash: bool = False) -> None:
    """Show the splash + picker; clears between iterations so each render
    starts from a clean screen (no last-action banners, no questionary
    residue from prior selections)."""

    while True:
        items = transcripts.list_transcripts()
        recording_active = recorder.is_recording()

        ui.console.clear()
        if not no_splash:
            ui.print_splash(len(items))

        if not items and not recording_active:
            ui.info("No transcripts yet. Try `whispertty rec` to make your first one.")
            return

        selection = ui.pick_transcript(items, recording_active=recording_active)

        if selection is None or selection == ui.ACTION_CANCEL:
            return

        if selection == ui.ACTION_RECORD:
            _record_via_picker(mode="mic")
            continue

        if selection == ui.ACTION_RECORD_SYSTEM:
            _record_via_picker(mode="system")
            continue

        if isinstance(selection, dict) and selection.get("_action") == "stop":
            _stop_via_picker()
            continue

        if selection == ui.ACTION_HELP:
            ui.show_help()
            continue

        # Selection is a Transcript — open it.
        _open_in_default_app(selection.path)


def _record_via_picker(*, mode: str) -> None:
    label = ui.prompt_label()
    if label is None:
        return
    label = label.strip() or None
    try:
        recorder.start(
            transcripts_dir=config.transcripts_dir(),
            mode=mode,
            label=label,
        )
    except (RuntimeError, ValueError) as e:
        ui.error(str(e))


def _stop_via_picker() -> None:
    try:
        meta = recorder.stop()
    except RuntimeError as e:
        ui.error(str(e))
        return
    _transcribe_and_finalize(meta)
    recorder.cleanup()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _open_in_default_app(path: Path | str) -> None:
    subprocess.run(["open", str(path)], check=False)


def _transcribe_and_finalize(meta) -> str | None:
    """Run Whisper on recorded files, build the merged transcript, return its
    path (or None on failure). Shows a Rich status spinner while Whisper
    runs since transcription can take 10s to several minutes."""
    model = config.get("whisper_model") or "base"
    out_dir = config.transcripts_dir()
    files = [Path(f) for f in meta.files]

    try:
        if meta.mode == "mic":
            with ui.console.status(
                f"[nav]Transcribing with Whisper ({model})...[/nav]",
                spinner="dots",
            ):
                txt = transcribe.transcribe_one_track(files[0], out_dir, model=model)
            if not config.get("keep_audio"):
                files[0].unlink(missing_ok=True)
            return str(txt)

        # system mode: two tracks
        remote_wav, local_wav = files[0], files[1]
        with ui.console.status(
            f"[nav]Transcribing remote track ({model})...[/nav]",
            spinner="dots",
        ):
            remote_json = transcribe.transcribe_file(remote_wav, out_dir, model=model)
        with ui.console.status(
            f"[nav]Transcribing local track ({model})...[/nav]",
            spinner="dots",
        ):
            local_json = transcribe.transcribe_file(local_wav, out_dir, model=model)

        # Merge to <stem>.txt where stem matches the timestamp+label without
        # the _remote / _local suffix.
        merged_stem = remote_wav.stem.removesuffix("_remote")
        final_txt = out_dir / f"{merged_stem}.txt"
        transcribe.merge_two_tracks(
            remote_json, local_json, final_txt,
            remote_label="Remote", local_label="Local",
        )

        # Tidy intermediates.
        for sidecar in (remote_json, local_json,
                        out_dir / f"{remote_wav.stem}.txt",
                        out_dir / f"{local_wav.stem}.txt"):
            sidecar.unlink(missing_ok=True)
        if not config.get("keep_audio"):
            remote_wav.unlink(missing_ok=True)
            local_wav.unlink(missing_ok=True)
        return str(final_txt)
    except Exception as e:
        ui.error(f"transcription failed: {e}")
        return None


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
