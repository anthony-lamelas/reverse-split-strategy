"""Shelling out to the Modal CLI, with the three things everyone forgets.

`scripts/dashboard.py` and `scripts/emergency_stop.py` both drive Modal as a
subprocess, and both had independently written the same call - so when the encoding
bug was found and fixed in one, the other kept it. That other one is the kill switch.

Three requirements, none of them obvious, all of them learned from a failure:

1. **Decode as UTF-8 explicitly.** `subprocess.run(text=True)` decodes with the
   PARENT's locale encoding, which is cp1252 here. Setting PYTHONIOENCODING in the
   child's environment does nothing about that - the child writes UTF-8 and the parent
   reads it as cp1252. The Modal CLI draws boxes, so the reader thread dies on the
   first error message, and a Modal failure surfaces as a UnicodeDecodeError traceback
   instead of as the failure it was reporting.

2. **Strip non-ASCII before displaying.** Even decoded correctly, echoing box-drawing
   characters to a cp1252 console raises UnicodeEncodeError on the way out. The kill
   switch was one `print` away from crashing while telling you the halt had failed.

3. **Pin the workspace per invocation.** `~/.modal.toml` is global and holds one
   active profile. Switching it to reach this repo's Volume breaks whatever other
   project owns the default, and switching back breaks this one. MODAL_PROFILE in the
   child's environment overrides the active profile without touching that file, so the
   global default can point elsewhere permanently.
"""
from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from typing import Optional

from . import config


def ascii_only(text: str) -> str:
    """Drop characters a legacy console cannot print.

    "ignore", not "replace": replacing turns a drawn box into a wall of '?' longer
    than the message inside it, which pushes the real error out of view.
    """
    return (text or "").encode("ascii", "ignore").decode("ascii")


def one_line(text: str, limit: int = 300) -> str:
    """Collapse CLI output to a single trimmed line, for banners and alerts."""
    return " ".join(ascii_only(text).split())[-limit:]


@dataclass
class ModalResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def combined(self) -> str:
        return (self.stdout or "") + (self.stderr or "")


def run(*args: str, timeout: Optional[float] = 180) -> ModalResult:
    """Run `python -m modal <args>`, pinned to this project's workspace.

    Returns a result rather than raising: every caller here is reporting on something
    that is already going wrong, and an exception thrown while reporting is strictly
    worse than a non-zero return code. A timeout or a failure to launch comes back as
    returncode 1 with the reason in `stderr`.
    """
    env = {**os.environ,
           "PYTHONIOENCODING": "utf-8",
           "PYTHONUTF8": "1",
           "MODAL_PROFILE": config.MODAL_PROFILE}
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "modal", *args],
            capture_output=True, env=env, timeout=timeout,
            encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        return ModalResult(1, "", f"modal timed out after {timeout}s")
    except Exception as e:
        return ModalResult(1, "", f"could not invoke modal: {str(e)[:200]}")
    return ModalResult(proc.returncode, proc.stdout or "", proc.stderr or "")
