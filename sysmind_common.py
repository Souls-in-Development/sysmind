"""Shared utilities for sysmind. Stdlib only."""
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import sysmind_platform

# Paths
CONFIG_DIR = Path.home().joinpath(*sysmind_platform.CURRENT.config_dir)
DATA_DIR = Path.home().joinpath(*sysmind_platform.CURRENT.data_dir)
CONFIG_FILE = CONFIG_DIR / "config.json"
ATLAS_FILE = DATA_DIR / "atlas.json"
BACKUP_DIR = DATA_DIR / "backups"

# Defaults
DEFAULT_CONFIG = {
    "level": 2,  # 1=Advisor, 2=Assistant, 3=Autopilot, 4=Full Auto
    "ram_tier": "8gb",  # 4gb, 8gb, 16gb, 32gb
    "model": "qwen3:8b",
    "language": "auto",  # auto = reply in whatever language the user writes; or an explicit name like "Urdu"/"English"/"Arabic"
    "budget": 4000,
    "paranoia": "medium",  # low, medium, high
    "notifications": "native",  # native, terminal, none
    "launch_method": "alias",  # alias, hotkey, both
    "display_dashboard": True,
    "blocklist_enabled": True,  # safety gate for destructive commands
    "approvals": {},  # allow list: tiered, narrow, base/sub/tertiary
    "blocklist": [],  # block list: what the human refused, matched broadly
    "usage_counts": {},  # how many times each command was executed
    "decisions": {},  # ledger: how often the human approved/rejected each thing
    "promote_threshold": 3,  # executions before suggesting promotion
}

# Pre-refused commands for THIS platform. Seeds the block list; the human can
# remove any of them. The Linux verbs protect nothing on Windows and vice
# versa, so this is platform data rather than a constant.
BLOCKLIST = sysmind_platform.CURRENT.blocklist_seed

SAFE_AUTO_COMMANDS = {
    "apt update", "apt upgrade", "apt autoremove",
    "systemctl restart", "logrotate", "journalctl --vacuum-time",
}


def ensure_dirs() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> Dict[str, Any]:
    ensure_dirs()
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            config = json.load(f)
        # Merge with defaults for any missing keys
        merged = dict(DEFAULT_CONFIG)
        merged.update(config)
        return merged
    return dict(DEFAULT_CONFIG)


def save_config(config: Dict[str, Any]) -> None:
    ensure_dirs()
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)


def run_cmd(cmd: List[str], check: bool = True, capture: bool = True) -> subprocess.CompletedProcess:
    """Run a command safely."""
    return subprocess.run(cmd, capture_output=capture, text=True, check=check)


def is_command_safe(cmd_str: str) -> bool:
    """Check if a command is in the safe auto-run whitelist."""
    cmd_lower = cmd_str.strip().lower()
    for safe in SAFE_AUTO_COMMANDS:
        if cmd_lower.startswith(safe.lower()):
            return True
    return False


def _significant_tokens(cmd_str: str) -> List[str]:
    """Command tokens with flags, wrappers and shell punctuation removed.

    Splitting on substitution and chaining characters matters: `$(rm -rf ~)`
    and `a && rm b` must both surface `rm` as a token.
    """
    tokens = []
    for raw in re.split(r'[\s;|&$()`{}<>]+', cmd_str.lower()):
        tok = raw.strip().strip('"\'')
        if not tok or tok.startswith("-"):
            continue
        if tok in ("sudo", "env", "command", "exec", "nohup"):
            continue          # wrappers must not hide what they wrap
        tokens.append(tok)
    return tokens


def contains_blocked(cmd_str: str, blocklist_enabled: bool = True,
                     user_blocklist: Optional[List[str]] = None) -> Optional[str]:
    """Return the block-list entry this command matches, else None.

    Two kinds of entry, both matched BROADLY — the opposite of how approvals
    are matched, because the safe direction is opposite:

      * single token ('rm')      matches that binary anywhere in the command,
                                 including as /bin/rm, sudo rm, or $(rm ...)
      * phrase ('apt install x') matches that run of tokens with flags ignored,
                                 so '--force' or '-y' cannot slip past it

    `blocklist_enabled` gates only the shipped seed. Entries the human declared
    themselves always apply — turning off the training wheels must not silently
    revoke your own decisions.
    """
    tokens = _significant_tokens(cmd_str)
    names = set(tokens) | {t.rsplit("/", 1)[-1] for t in tokens}

    entries = list(user_blocklist or [])
    if blocklist_enabled:
        entries.extend(BLOCKLIST)

    for entry in entries:
        parts = _significant_tokens(entry)
        if not parts:
            continue
        if len(parts) == 1:
            if parts[0] in names:
                return entry
        else:
            n = len(parts)
            if any(tokens[i:i + n] == parts
                   for i in range(len(tokens) - n + 1)):
                return entry
    return None


def classify_command(cmd_str: str) -> Tuple[str, str, str]:
    """Return (base, sub, full) taxonomy for a command.
    
    base  = first token (e.g. 'apt', 'systemctl')
    sub   = base:first-arg (e.g. 'apt:update', 'systemctl:restart')
    full  = complete command string (tertiary)
    """
    cmd = cmd_str.strip().lower()
    tokens = cmd.split()
    if not tokens:
        return ("", "", "")
    
    base = tokens[0]
    
    # Subcommand: base + first non-flag argument
    sub = base
    if len(tokens) > 1:
        for tok in tokens[1:]:
            if not tok.startswith("-"):
                sub = f"{base}:{tok}"
                break
    
    full = cmd
    return (base, sub, full)


def is_approved(cmd_str: str, level: int, approvals: Dict[str, Any]) -> Tuple[bool, str]:
    """Check if command is approved at current level.
    
    Tiers are INDEPENDENT — base does NOT grant sub or tertiary.
    
    Returns (approved, reason_string)
    """
    base, sub, full = classify_command(cmd_str)
    
    # Tertiary: exact command match
    if full in approvals:
        app_level = approvals[full].get("level", 99)
        if app_level <= level:
            return True, f"tertiary '{full}'"
    
    # Sub: only if this command HAS a subcommand (base != sub)
    if sub != base and sub in approvals:
        app_level = approvals[sub].get("level", 99)
        if app_level <= level:
            return True, f"sub '{sub}'"
    
    # Base: only for commands with NO subcommand (base == sub == full)
    if base == sub == full and base in approvals:
        app_level = approvals[base].get("level", 99)
        if app_level <= level:
            return True, f"base '{base}'"
    
    return False, "not approved"


def save_approval(config: Dict[str, Any], cmd_str: str, tier: str, level: int) -> str:
    """Save an approval at the given tier and level.
    
    tier: 'base', 'sub', or 'tertiary'
    Returns the key that was approved.
    """
    base, sub, full = classify_command(cmd_str)
    
    if tier == "tertiary":
        key = full
    elif tier == "sub":
        key = sub
    else:
        key = base
    
    if "approvals" not in config:
        config["approvals"] = {}
    
    config["approvals"][key] = {"level": level}
    save_config(config)
    return key


def save_block(config: Dict[str, Any], cmd_str: str) -> str:
    """Add the human's 'never' to the block list.

    The counterpart to save_approval — but it goes in the block list, not a
    mirror of the allow list, so it is matched broadly and a varied form of the
    same command cannot slip past it.
    """
    entry = " ".join(_significant_tokens(cmd_str))
    if not entry:
        entry = cmd_str.strip().lower()

    blocklist = config.setdefault("blocklist", [])
    if entry not in blocklist:
        blocklist.append(entry)
        save_config(config)
    return entry


def natural_tier(cmd_str: str) -> str:
    """Determine the natural tier for asking approval.
    
    Bare commands (no args) → base
    Commands with subcommands/args → tertiary
    """
    base, sub, full = classify_command(cmd_str)
    if base == sub == full:
        return "base"
    return "tertiary"


def log_usage(config: Dict[str, Any], cmd_str: str) -> None:
    """Track how many times a command pattern was executed."""
    base, sub, full = classify_command(cmd_str)
    
    if "usage_counts" not in config:
        config["usage_counts"] = {}
    
    # Track by subcommand (e.g., apt:update, systemctl:restart)
    if base != sub:
        key = sub
    else:
        key = base
    
    config["usage_counts"][key] = config["usage_counts"].get(key, 0) + 1
    save_config(config)


def log_decision(config: Dict[str, Any], cmd_str: str, outcome: str) -> None:
    """Record that the human approved or rejected this, at exact and family level.

    Only called when the human was actually *asked*. An auto-approved run is an
    execution, not a decision — conflating them would let repetition masquerade
    as consent.

    This ledger records; it never rules. Nothing here converts a count into a
    permission: the onus stays on the human to make something known.
    """
    if outcome not in ("approved", "rejected"):
        raise ValueError("outcome must be 'approved' or 'rejected'")

    base, sub, full = classify_command(cmd_str)
    if "decisions" not in config:
        config["decisions"] = {}

    keys = {full}
    if sub != base:
        keys.add(sub)          # also track the family, e.g. 'apt:install'

    for key in keys:
        rec = config["decisions"].setdefault(key, {"approved": 0, "rejected": 0})
        rec[outcome] = rec.get(outcome, 0) + 1

    save_config(config)


def decision_history(config: Dict[str, Any],
                     cmd_str: str) -> Tuple[Optional[dict], Optional[dict]]:
    """Return (exact_record, family_record); either may be None if never asked."""
    base, sub, full = classify_command(cmd_str)
    ledger = config.get("decisions", {})
    exact = ledger.get(full)
    family = ledger.get(sub) if sub != base else None
    return exact, family


def format_history(config: Dict[str, Any], cmd_str: str) -> str:
    """One line telling the human what they decided about this before."""
    exact, family = decision_history(config, cmd_str)
    base, sub, full = classify_command(cmd_str)

    parts = []
    if exact:
        parts.append("this exact command: {}x approved, {}x rejected".format(
            exact.get("approved", 0), exact.get("rejected", 0)))
    if family:
        parts.append("'{}': {}x approved, {}x rejected".format(
            sub, family.get("approved", 0), family.get("rejected", 0)))
    return "  |  ".join(parts) if parts else "never asked about this before"


def suggest_promotion(config: Dict[str, Any], cmd_str: str) -> Tuple[bool, str]:
    """Check if promotion from tertiary to sub is warranted.
    
    Returns (should_suggest, message)
    """
    base, sub, full = classify_command(cmd_str)
    
    # No meaningful subcommand → no promotion
    if base == sub:
        return False, ""

    # Already permitted at sub → nothing to widen
    approvals = config.get("approvals", {})
    if sub in approvals:
        return False, ""

    # Counts APPROVALS THE HUMAN GAVE, not executions. An auto-approved run is
    # the machine repeating itself; it must never become evidence for granting
    # the machine more scope.
    rec = config.get("decisions", {}).get(sub, {})
    approved = rec.get("approved", 0)
    rejected = rec.get("rejected", 0)
    threshold = config.get("promote_threshold", 3)

    if approved >= threshold:
        msg = "You've approved '{}' {}x".format(sub, approved)
        if rejected:
            msg += " and rejected it {}x".format(rejected)
        return True, msg + ". Permit '{}' from now on? [y/N]".format(sub)

    return False, ""
