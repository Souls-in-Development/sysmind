"""What the machine's language is — the unconscious slot's counterpart to the
conscious slot's human language.

The conscious slot's language comes from a curated table of model families, not
from code that special-cases Urdu. This is the same thing for the machine side:
the OS-specific facts live here as data, and nothing else in the codebase knows
what an operating system is.

Built by EXTRACTION, not speculation. Everything in the linux profile was
already hardcoded somewhere and was moved here unchanged. The windows profile
is written from documented APIs but has never been run — `tested = False` says
so, and the code surfaces it rather than leaving it in a comment.

What is NOT here yet: the scan probes. Parsing `df -h` is not just the command,
it is the output format, so a second platform needs its own scanner rather than
a table entry. sysmind_scan stays Linux-specific and honest about it.
"""
from __future__ import annotations

import platform as _platform
import subprocess
from dataclasses import dataclass, field
from typing import List, Optional, Set


@dataclass(frozen=True)
class Platform:
    key: str
    name: str
    tested: bool

    # How a command is handed to the system.
    shell: List[str]

    # argv that PARSES stdin and exits non-zero on a syntax error, without
    # executing anything. None means this platform has no such checker, which
    # is treated as "cannot verify" and fails closed.
    syntax_check: Optional[List[str]]

    # Fence languages whose contents are runnable on this platform.
    fences: Set[str]

    # Pre-refused commands. Seeds the block list; the human can remove them.
    blocklist_seed: Set[str]

    # Command names recognised as real commands, used to tell shell apart from
    # prose that merely parses.
    known_commands: Set[str]

    # Where config and data live, by this OS's convention.
    config_dir: List[str]      # path parts under the home directory
    data_dir: List[str]

    # Calibration probes. The unconscious battery must ask for THIS platform's
    # dialect: a coder scored on bash is not scored on PowerShell.
    reasoning_tasks: List[str]
    command_tasks: List[str]


LINUX = Platform(
    key="linux",
    name="Linux",
    tested=True,
    shell=["/bin/sh", "-c"],
    syntax_check=["bash", "-n"],
    fences={"bash", "sh", "shell", "zsh", "console"},
    blocklist_seed={
        "rm", "dd", "mkfs", "fdisk", "mkfs.ext4", "mkfs.ext3",
        "mkfs.ntfs", "mkfs.vfat", "parted", "gparted",
    },
    known_commands={
        "ls", "du", "df", "find", "grep", "egrep", "awk", "sed", "sort", "head",
        "tail", "cat", "echo", "wc", "cut", "tr", "uniq", "xargs", "tee",
        "systemctl", "journalctl", "service", "systemd-analyze", "loginctl",
        "apt", "apt-get", "apt-cache", "dpkg", "dpkg-query", "snap", "flatpak",
        "ss", "netstat", "ip", "ping", "curl", "wget", "dig", "host",
        "ps", "top", "htop", "free", "uptime", "uname", "whoami", "id", "last",
        "who", "lsof", "chmod", "chown", "stat", "file", "which", "whereis",
        "readlink", "realpath", "tar", "gzip", "zip", "unzip", "rsync", "cp",
        "mv", "mkdir", "touch", "ln", "kill", "pkill", "pgrep", "nice", "renice",
        "timeout", "sudo", "env", "date", "sync", "mount", "lsblk", "blkid",
    },
    config_dir=[".config", "sysmind"],
    data_dir=[".local", "share", "sysmind"],
    reasoning_tasks=[
        "The root partition on a Debian laptop is 94% full. Explain how you "
        "would work out what is consuming it and what is safe to remove. Do "
        "not give commands, explain the approach.",
        "Compare running a periodic task as a systemd timer versus a cron job "
        "on a personal Linux machine. What are the practical consequences?",
        "A systemd service fails on boot but starts fine manually afterwards. "
        "Explain the likely causes and how you would narrow them down.",
    ],
    command_tasks=[
        "# shell one-liner: print the ten largest directories under /var, "
        "human readable, largest first\n",
        "# shell one-liner: show every systemd unit in a failed state, no "
        "pager, one per line\n",
        "# shell one-liner: list packages with available upgrades, then count "
        "them\n",
    ],
)

# PowerShell's parser exposes ParseInput, which populates an error list without
# executing anything — a genuine equivalent of `bash -n`, so the syntax gate
# survives here rather than being silently lost.
_PS_PARSE = (
    "$c = [Console]::In.ReadToEnd(); $e = $null; "
    "[void][System.Management.Automation.Language.Parser]::ParseInput("
    "$c, [ref]$null, [ref]$e); "
    "if ($e.Count -gt 0) { exit 1 } else { exit 0 }"
)

WINDOWS = Platform(
    key="windows",
    name="Windows",
    tested=False,          # never run; surfaced to the user, not buried here
    shell=["powershell", "-NoProfile", "-Command"],
    syntax_check=["powershell", "-NoProfile", "-Command", _PS_PARSE],
    fences={"powershell", "ps1", "pwsh", "cmd", "bat", "batch"},
    blocklist_seed={
        # The Linux seed protects nothing here. These are the Windows verbs
        # that are unrecoverable.
        "format", "diskpart", "del", "rd", "rmdir", "cipher",
        "remove-item", "clear-disk", "format-volume", "remove-partition",
        "vssadmin", "bcdedit",
    },
    known_commands={
        "get-childitem", "get-content", "get-service", "get-process",
        "get-volume", "get-disk", "get-eventlog", "get-winevent",
        "get-hotfix", "get-computerinfo", "get-netadapter", "get-nettcpconnection",
        "select-object", "where-object", "sort-object", "measure-object",
        "foreach-object", "format-table", "format-list", "out-string",
        "start-service", "stop-service", "restart-service", "set-service",
        "test-connection", "resolve-dnsname", "invoke-webrequest",
        "winget", "dism", "sfc", "chkdsk", "ipconfig", "netstat", "tasklist",
        "systeminfo", "dir", "type", "findstr", "where", "sc", "reg",
    },
    config_dir=["AppData", "Roaming", "sysmind"],
    data_dir=["AppData", "Local", "sysmind"],
    reasoning_tasks=[
        "The system drive on a Windows laptop is 94% full. Explain how you "
        "would work out what is consuming it and what is safe to remove. Do "
        "not give commands, explain the approach.",
        "Compare running a periodic job as a Scheduled Task versus a Windows "
        "service on a personal machine. What are the practical consequences?",
        "A Windows service set to Automatic fails at boot but starts fine "
        "manually afterwards. Explain the likely causes and how you would "
        "narrow them down.",
    ],
    command_tasks=[
        "# PowerShell one-liner: list the ten largest folders under "
        "C:\\ProgramData, largest first\n",
        "# PowerShell one-liner: show every automatic service that is not "
        "running, one per line\n",
        "# PowerShell one-liner: list available package upgrades with winget, "
        "then count them\n",
    ],
)

UNKNOWN = Platform(
    key="unknown",
    name="unrecognised system",
    tested=False,
    shell=["/bin/sh", "-c"],
    syntax_check=None,     # no checker -> everything fails closed
    fences=set(),
    blocklist_seed=set(),
    known_commands=set(),
    config_dir=[".config", "sysmind"],
    data_dir=[".local", "share", "sysmind"],
    reasoning_tasks=[],
    command_tasks=[],
)

PROFILES = {p.key: p for p in (LINUX, WINDOWS)}


def detect() -> Platform:
    system = _platform.system().lower()
    if system == "linux":
        return LINUX
    if system == "windows":
        return WINDOWS
    if system == "darwin":
        # macOS is close enough to run and test the Linux profile's shell layer,
        # which is how this codebase is developed. Its scan probes differ, so it
        # is not claimed as tested.
        return LINUX
    return UNKNOWN


CURRENT = detect()


def syntax_ok(code: str, plat: Optional[Platform] = None,
              timeout: float = 5.0) -> Optional[bool]:
    """True = parses, False = syntax error, None = COULD NOT CHECK.

    None is not "fine". Callers that are about to run something must treat it
    as a refusal: an unverifiable command on a machine with no checker is
    exactly the case where the gate must hold, not evaporate.
    """
    plat = plat or CURRENT
    if not code.strip() or not plat.syntax_check:
        return None
    try:
        r = subprocess.run(plat.syntax_check, input=code, text=True,
                           capture_output=True, timeout=timeout)
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None


def checker_available(plat: Optional[Platform] = None) -> bool:
    """Is a syntax checker actually present? Distinguishes 'invalid command'
    from 'this machine cannot check commands', which need different messages."""
    plat = plat or CURRENT
    if not plat.syntax_check:
        return False
    return syntax_ok("true", plat) is not None
