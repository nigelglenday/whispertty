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

from . import config, diarize, recorder, transcribe, transcripts, ui


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
    _warn_if_suspicious_input()
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
    ui.console.print(f"[soft]Input:  {meta.input_device or '(unknown)'}[/soft]")
    if meta.prev_output and meta.mode == "system":
        ui.console.print(
            f"[soft]Output: {meta.prev_output} → {recorder.MULTI_OUTPUT_NAME}[/soft]"
        )
    for f in meta.files:
        ui.console.print(f"[soft]  {f}[/soft]")
    ui.console.print("\n[nav]Run `whispertty stop` when done.[/nav]")


def _warn_if_suspicious_input() -> None:
    """Warn (but don't block) if the system's current input device looks
    like one that would record silence instead of voice."""
    current = recorder.get_current_input()
    if recorder.is_suspicious_input(current):
        ui.warn(
            f"Heads up: system input is '{current}', which is unlikely to capture voice."
        )
        ui.console.print(
            "[soft]Switch in System Settings → Sound → Input, or run:[/soft]"
        )
        ui.console.print(
            '[soft]  SwitchAudioSource -t input -s "MacBook Air Microphone"[/soft]'
        )


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
        if config.get("auto_copy_on_stop"):
            try:
                text = Path(final_txt).read_text()
                if _copy_to_clipboard(text):
                    ui.info(f"Copied to clipboard ({len(text)} chars).")
            except OSError:
                pass
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
    if meta.input_device:
        ui.console.print(f"  input:    [nav]{meta.input_device}[/nav]")
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


@cli.command("diarize")
@click.argument("stem")
def diarize_cmd(stem: str) -> None:
    """Run speaker diarization on an existing recording. Replaces the
    plain transcript with a labeled version (Speaker_00 / Speaker_01 / ...).

    Requires hf_token to be set:
        whispertty config hf_token hf_xxxxx
    """
    if stem.endswith(".txt"):
        stem = stem[:-4]
    t = transcripts.find(stem)
    if not t:
        ui.error(f"transcript not found: {stem}")
        sys.exit(1)
    msg = _diarize_transcript(t)
    if msg.startswith("✗"):
        sys.exit(1)


def _diarize_transcript(t) -> str:
    """Run diarization on transcript `t`. Returns a status string suitable
    for the picker last-action banner."""
    token = config.get("hf_token")
    if not token:
        ui.error("hf_token not set. To enable diarization:")
        ui.console.print(
            "[soft]  1. Create a free HF account at https://huggingface.co[/soft]"
        )
        ui.console.print(
            "[soft]  2. Accept terms at https://huggingface.co/pyannote/speaker-diarization-3.1[/soft]"
        )
        ui.console.print(
            "[soft]  3. Accept terms at https://huggingface.co/pyannote/segmentation-3.0[/soft]"
        )
        ui.console.print(
            "[soft]  4. Accept terms at https://huggingface.co/pyannote/speaker-diarization-community-1[/soft]"
        )
        ui.console.print(
            "[soft]  5. Generate read token at https://huggingface.co/settings/tokens[/soft]"
        )
        ui.console.print(
            '[soft]  6. whispertty config hf_token "hf_xxxxx"[/soft]'
        )
        return "✗ hf_token not set"

    wav = t.path.with_suffix(".wav")
    if not wav.exists():
        ui.error(f"audio file not found: {wav}")
        return f"✗ audio missing for {t.stem}"

    json_path = t.path.with_suffix(".json")
    out_dir = config.transcripts_dir()
    model = config.get("whisper_model") or "base"

    # Re-transcribe to get JSON if the sidecar isn't there (older recordings
    # made before we started saving JSON alongside).
    if not json_path.exists():
        try:
            backend = transcribe.resolve_backend(config.get("whisper_backend") or "auto")
        except RuntimeError as e:
            ui.error(str(e))
            return f"✗ {e}"
        ui.info(f"No JSON sidecar found. Re-transcribing {t.stem}...")
        try:
            transcribe.transcribe_file(
                wav, out_dir, model=model, backend=backend,
                console=ui.console,
                description=f"Transcribing ({backend} · {model})",
            )
        except RuntimeError as e:
            ui.error(str(e))
            return f"✗ transcription failed"

    # Run pyannote diarization.
    try:
        with ui.console.status(
            "[nav]Running speaker diarization (pyannote)...[/nav]",
            spinner="dots",
        ):
            diar_segments = diarize.diarize_audio(wav, token)
    except Exception as e:
        ui.error(f"diarization failed: {e}")
        return f"✗ diarization failed"

    # Merge into a labeled .txt, replacing the plain version.
    try:
        diarize.merge(json_path, diar_segments, t.path)
    except Exception as e:
        ui.error(f"merge failed: {e}")
        return f"✗ merge failed"

    n_speakers = len({s["speaker"] for s in diar_segments})
    return f"✓ Diarized '{t.stem}' ({n_speakers} speaker{'s' if n_speakers != 1 else ''})"


@cli.command("cp")
@click.argument("stem")
def cp_cmd(stem: str) -> None:
    """Copy a transcript's text to the clipboard."""
    if stem.endswith(".txt"):
        stem = stem[:-4]
    t = transcripts.find(stem)
    if not t:
        ui.error(f"transcript not found: {stem}")
        sys.exit(1)
    try:
        text = t.path.read_text()
    except OSError as e:
        ui.error(f"could not read {t.path}: {e}")
        sys.exit(1)
    if not _copy_to_clipboard(text):
        ui.error("pbcopy failed; clipboard not updated.")
        sys.exit(1)
    ui.info(f"Copied '{t.stem}' to clipboard ({len(text)} chars).")


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
    """Show the splash + picker; clears between iterations.

    Enter on a transcript copies its text to the clipboard (the common
    workflow: paste into a context-rich agent for next actions). The
    'Copied X' confirmation persists for one iteration since clipboard
    state isn't visible elsewhere.
    """
    last_action: str | None = None

    while True:
        items = transcripts.list_transcripts()
        recording_active = recorder.is_recording()

        ui.console.clear()
        if not no_splash:
            ui.print_splash(len(items))

        if last_action:
            ui.console.print(f"  [accent]✓[/accent] [nav]{last_action}[/nav]\n")
            last_action = None

        if not items and not recording_active:
            ui.console.print(
                "  [soft]No transcripts yet. Pick 'Record now' below to make your first one.[/soft]\n"
            )

        selection = ui.pick_transcript(items, recording_active=recording_active)

        if selection is None or selection == ui.ACTION_CANCEL:
            return

        if selection == ui.ACTION_RECORD:
            last_action = _record_via_picker(mode="mic")
            continue

        if selection == ui.ACTION_RECORD_SYSTEM:
            last_action = _record_via_picker(mode="system")
            continue

        if isinstance(selection, dict) and selection.get("_action") == "stop":
            last_action = _stop_via_picker()
            continue

        if selection == ui.ACTION_HELP:
            ui.show_help()
            continue

        # Selection is a Transcript — show preview + actions.
        last_action = _transcript_action_flow(selection)


def _transcript_action_flow(t) -> str | None:
    """Stay on the transcript view and let the user perform multiple actions
    in a row. Only Delete (after confirmation) and Back exit the flow."""
    feedback: str | None = None

    while True:
        action = ui.show_transcript_actions(t, feedback=feedback)
        feedback = None  # consumed; clear for next iteration

        if action in (None, "back"):
            return None

        if action == "copy":
            try:
                text = t.path.read_text()
            except OSError as e:
                feedback = f"✗ Could not read: {e}"
                continue
            if _copy_to_clipboard(text):
                feedback = f"✓ Copied! ({len(text)} chars on clipboard)"
            else:
                feedback = "✗ pbcopy failed"
            continue

        if action == "open":
            _open_in_default_app(t.path)
            feedback = "✓ Opened in default app"
            continue

        if action == "reveal":
            subprocess.run(["open", "-R", str(t.path)], check=False)
            feedback = "✓ Revealed in Finder"
            continue

        if action == "diarize":
            feedback = _diarize_transcript(t)
            # Re-load transcript metadata since we just rewrote the .txt
            refreshed = transcripts.find(t.stem)
            if refreshed:
                t = refreshed
            continue

        if action == "delete":
            if not ui.confirm_delete(t):
                feedback = "Delete cancelled"
                continue
            removed = transcripts.delete(t.stem)
            return (
                f"Deleted '{t.stem}' "
                f"({len(removed)} file{'s' if len(removed) != 1 else ''})"
            )


def _copy_to_clipboard(text: str) -> bool:
    try:
        subprocess.run(["pbcopy"], input=text, text=True, check=True)
        return True
    except subprocess.SubprocessError:
        return False


def _copy_transcript_to_clipboard(t) -> str | None:
    """Read transcript file, push to clipboard, return a status message
    suitable for the 'last_action' banner."""
    try:
        text = t.path.read_text()
    except OSError as e:
        ui.error(f"could not read {t.path}: {e}")
        return None
    if _copy_to_clipboard(text):
        return f"Copied '{t.stem}' to clipboard ({len(text)} chars)"
    ui.error("pbcopy failed; clipboard not updated.")
    return None


def _record_via_picker(*, mode: str) -> str | None:
    label = ui.prompt_label()
    if label is None:
        return None
    label = label.strip() or None
    _warn_if_suspicious_input()
    try:
        meta = recorder.start(
            transcripts_dir=config.transcripts_dir(),
            mode=mode,
            label=label,
        )
    except (RuntimeError, ValueError) as e:
        ui.error(str(e))
        return None

    # Block with a live counter; user presses any key to stop.
    ui.live_recording_meter(meta)

    try:
        meta = recorder.stop()
    except RuntimeError as e:
        ui.error(str(e))
        return None
    final_txt = _transcribe_and_finalize(meta)
    recorder.cleanup()
    return _autocopy_after_stop(final_txt)


def _stop_via_picker() -> str | None:
    """Used when the picker is opened with a recording already in progress
    (started from a different terminal / via `whispertty rec`)."""
    try:
        meta = recorder.stop()
    except RuntimeError as e:
        ui.error(str(e))
        return None
    final_txt = _transcribe_and_finalize(meta)
    recorder.cleanup()
    return _autocopy_after_stop(final_txt)


def _autocopy_after_stop(final_txt: str | None) -> str | None:
    """If a transcript was produced and auto_copy_on_stop is enabled, copy
    its text to the clipboard. Returns a status string for the picker
    last-action banner (or None)."""
    if not final_txt:
        return None
    name = Path(final_txt).name
    if config.get("auto_copy_on_stop"):
        try:
            text = Path(final_txt).read_text()
            if _copy_to_clipboard(text):
                return f"Saved & copied to clipboard: {name} ({len(text)} chars)"
        except OSError:
            pass
    return f"Saved: {name}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _open_in_default_app(path: Path | str) -> None:
    subprocess.run(["open", str(path)], check=False)


def _transcribe_and_finalize(meta) -> str | None:
    """Run Whisper on recorded files, build the merged transcript, return its
    path (or None on failure). Shows a Rich Progress bar driven by Whisper's
    timestamp stream — gives both percentage done and estimated time
    remaining."""
    model = config.get("whisper_model") or "base"
    backend_pref = config.get("whisper_backend") or "auto"
    try:
        backend = transcribe.resolve_backend(backend_pref)
    except RuntimeError as e:
        ui.error(str(e))
        return None
    desc_suffix = f"{backend} · {model}"
    out_dir = config.transcripts_dir()
    files = [Path(f) for f in meta.files]

    try:
        if meta.mode == "mic":
            txt = transcribe.transcribe_one_track(
                files[0], out_dir, model=model, backend=backend,
                console=ui.console,
                description=f"Transcribing ({desc_suffix})",
            )
            if not config.get("keep_audio"):
                files[0].unlink(missing_ok=True)
            return str(txt)

        # system mode: two tracks
        remote_wav, local_wav = files[0], files[1]
        remote_json = transcribe.transcribe_file(
            remote_wav, out_dir, model=model, backend=backend,
            console=ui.console,
            description=f"Transcribing remote ({desc_suffix})",
        )
        local_json = transcribe.transcribe_file(
            local_wav, out_dir, model=model, backend=backend,
            console=ui.console,
            description=f"Transcribing local ({desc_suffix})",
        )

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
