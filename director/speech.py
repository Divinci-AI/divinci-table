"""Optional speech for the commentators, with the Mac's own `say` (no cloud voice). OFF unless commentary.speech.enabled.

Two things to know before turning it on (docs/broadcast-director.md says more):
  - Where the sound goes. The line is written to a WAV file and PLAYED BY THE DIRECTOR PAGE inside OBS's browser source. With that
    source's "Control audio via OBS" ticked and its monitoring off, OBS puts the sound in the stream mix and not on the laptop's
    speakers. This program cannot check that setting: it is the operator's to tick and to listen for.
  - Apple's system voices on a public stream are an open licence question (the stream plan lists it). Nothing here settles it.

A hand-informed line about a table where a person is playing is never spoken (may_speak), whatever the routing: a person at the laptop
could hear it, and a remote player hears the stream."""
from __future__ import annotations

import subprocess
from pathlib import Path


def may_speak(line: dict, human_tables_on_air: bool) -> bool:
    return not (line.get("hand_informed") and human_tables_on_air)


class Say:
    def __init__(self, out_dir: str, voices: dict | None = None, runner=subprocess.run, keep: int = 12):
        self.dir = Path(out_dir); self.dir.mkdir(parents=True, exist_ok=True)
        self.voices, self.runner, self.keep = voices or {}, runner, keep
        self.made: list = []

    def render(self, line: dict) -> str | None:
        """Write the line to <id>.wav and return the file name, or None if `say` failed. The text is passed as an argument list (no
        shell), after "--", so a line can never be read as an option."""
        out = self.dir / f"{int(line['id'])}.wav"
        cmd = ["/usr/bin/say", "-o", str(out), "--file-format=WAVE", "--data-format=LEI16@22050"]
        v = self.voices.get(line.get("voice"))
        if v:
            cmd += ["-v", str(v)]
        try:
            r = self.runner(cmd + ["--", line["text"]], capture_output=True, timeout=20)
        except Exception:  # noqa: BLE001
            return None
        if getattr(r, "returncode", 1) != 0:
            return None
        self.made.append(out)
        for old in self.made[:-self.keep]:
            old.unlink(missing_ok=True)
        self.made = self.made[-self.keep:]
        return out.name
