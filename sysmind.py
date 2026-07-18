"""Main orchestrator. Menu, AI, execution."""
import json
import os
import subprocess
import sys
from pathlib import Path

from sysmind_common import (load_config, save_config, is_command_safe,
                            contains_blocked, classify_command, is_approved,
                            save_approval, natural_tier, log_usage,
                            suggest_promotion, run_cmd, ATLAS_FILE,
                            log_decision, format_history,
                            save_block, CONFIG_FILE)
from sysmind_scan import scan
from sysmind_orbit import build_context
from sysmind_display import render_dashboard
from sysmind_sync import shell_syntax_ok
from sysmind_turn import run_turn, needs_ollama
import sysmind_strings as strings


# Fence languages treated as runnable shell. A bare ``` is deliberately NOT
# included: it is ambiguous, and widening what counts as executable is the
# wrong direction for a tool that runs what it extracts.
SHELL_FENCES = {"bash", "sh", "shell", "zsh", "console"}


def extract_command_blocks(response: str) -> list:
    """Return each fenced shell block as ONE command.

    The previous version appended each *line* separately, so any multi-line
    command was shattered and its fragments executed independently — a `for`
    loop ran as `for x in ...; do`, then the body with an unset variable, then
    a bare `done`. A block is one unit; it is extracted and run as one.
    """
    blocks = []
    current = []
    in_fence = False
    is_shell = False

    for line in response.splitlines():
        if line.strip().startswith("```"):
            if not in_fence:
                lang = line.strip()[3:].strip().lower()
                in_fence, is_shell, current = True, lang in SHELL_FENCES, []
            else:
                if is_shell:
                    block = "\n".join(current).strip()
                    if block:
                        blocks.append(block)
                in_fence, is_shell, current = False, False, []
            continue
        if in_fence and is_shell:
            current.append(line)

    if in_fence and is_shell:            # unterminated fence
        block = "\n".join(current).strip()
        if block:
            blocks.append(block)
    return blocks


def _display(cmd: str) -> str:
    """Indent a possibly multi-line command so it reads as one unit."""
    lines = cmd.splitlines()
    if len(lines) <= 1:
        return cmd
    return "\n" + "\n".join("    " + ln for ln in lines)


# Menu strings remain English for now; AI replies follow config["language"]
MENU = """
🧠 Parrot System Mind
─────────────────────────────
[1] Check for updates & upgrades
[2] Check security posture
[3] Free up disk space
[4] Review recent logins & activity
[5] Fix/restart a service
[6] Ask something specific
[7] Show system summary
[q] Quit
"""

MENU_MAP = {
    "1": "Check for updates and upgrades",
    "2": "Check security posture",
    "3": "Free up disk space",
    "4": "Review recent logins and activity",
    "5": "Fix or restart a service",
    "6": "Ask something specific",
    "7": "Show system summary",
}


def check_ollama() -> bool:
    try:
        r = run_cmd(["which", "ollama"], check=False)
        return r.returncode == 0
    except Exception:
        return False


def query_ollama(model: str, context: str, language: str = "auto") -> str:
    if language == "auto":
        lang_instruction = "Reply in the SAME language the user wrote their request in."
    else:
        lang_instruction = f"Always reply in {language}."
    prompt = f"{context}\n\nYou are a helpful Linux system administrator. Answer concisely. If suggesting a command, wrap it in ```bash blocks. Always explain what a command does before suggesting it. {lang_instruction} Only your prose and explanations follow that language rule — shell commands inside ```bash blocks must stay exactly as they must be typed: valid shell, never translated, never altered.\n\nProvide your response."
    try:
        r = run_cmd(["ollama", "run", model, prompt], check=False, capture=True)
        return r.stdout.strip()
    except Exception as e:
        return f"Error querying Ollama: {e}\n\nIs Ollama installed and the model downloaded? Run: ollama pull {model}"


def execute_with_confirm(cmd: str, level: int, config: dict,
                         asked: str = None) -> None:
    blocklist_enabled = config.get("blocklist_enabled", True)
    approvals = config.get("approvals", {})

    # 0. Never offer something that is not valid shell. `bash -n` parses
    #    without executing, so this is safe to run on model output.
    if shell_syntax_ok(cmd) is False:
        print("⚠️  " + strings.load(config)["invalid_shell"])
        print(f"   {_display(cmd)}")
        return

    # 1. Block list: the shipped seed plus everything the human has refused.
    #    Checked before approvals — a declared 'no' outranks a standing 'yes'.
    #    blocklist_enabled gates only the seed; your own entries always hold.
    user_blocklist = config.get("blocklist", [])
    blocked = contains_blocked(cmd, blocklist_enabled, user_blocklist)
    if blocked:
        if blocked in user_blocklist:
            print(f"✗ {strings.load(config)['refused_before']} ('{blocked}').")
        else:
            print(f"⚠️  {strings.load(config)['blocked_seed']} ('{blocked}').")
            print("   To drop the shipped defaults: blocklist_enabled: false")
        print(f"   {_display(cmd)}")
        print(f"   To allow it again, edit 'blocklist' in {CONFIG_FILE}")
        return

    # 2. Allow list. This path asks NOTHING — not asking is the entire point
    #    of having made it known — so nothing here may prompt. It must also
    #    stay silent because it is the path that runs unattended.
    approved, reason = is_approved(cmd, level, approvals)
    if approved:
        print(f"✓ {strings.load(config)['auto_approved']} ({reason}): {_display(cmd)}")
        print(f"$ {_display(cmd)}")
        os.system(cmd)
        log_usage(config, cmd)
        return

    # 3. Neither known-refused nor known-permitted — ask, so the human
    #    can make it known one way or the other.
    base, sub, full = classify_command(cmd)
    tier = natural_tier(cmd)
    
    if level == 1:
        # Advisor: just show, don't offer approval
        print(f"\nSuggested: {_display(cmd)}")
        print("(Advisor mode — copy-paste to run manually)")
        return
    
    S = strings.load(config)

    print(f"\n{_display(cmd)}")
    print(f"  {S['history']}: {format_history(config, cmd)}")
    if tier != "base":
        print(f"  base: {base}  |  sub: {sub}  |  full: {full[:60]}")

    # The unconscious wants to run this; the conscious asks the human, in
    # their language, about this specific command. The cached generic question
    # is the fallback for when the conscious slot could not be reached.
    print(f"\n{asked or S['ask_question']}")
    for key, label in (("1", "opt_once"), ("2", "opt_no"),
                       ("3", "opt_always"), ("4", "opt_never")):
        print(strings.option_line(S, key, label))

    # Digits, not words: the model renders the labels but never reads the
    # answer, so a translation can confuse but cannot execute the wrong thing.
    answer = input(f"{S['choose']} [1]: ").strip().lower()
    confirm = {"1": "y", "2": "n", "3": "always", "4": "never",
               "": "y"}.get(answer, answer)

    if confirm in ("y", "yes", ""):
        log_decision(config, cmd, "approved")
        print(f"$ {_display(cmd)}")
        os.system(cmd)
        log_usage(config, cmd)
        # Offered only here: a human answered, so a human is present. Never on
        # the auto path, which must stay silent and unattended-safe.
        should_promote, promo_msg = suggest_promotion(config, cmd)
        if should_promote:
            if input(f"\n📈 {promo_msg} ").strip().lower() in ("y", "yes"):
                key = save_approval(config, cmd, "sub", level)
                print(f"✓ Permitted at sub level '{key}' for level {level}+")
    elif confirm in ("a", "always"):
        log_decision(config, cmd, "approved")
        # Breadth is chosen HERE, by the human, at the moment of declaring —
        # not inferred later from how often the machine ran it.
        scope = tier
        if sub != base:
            print(f"  Permit  [1] only this exact command")
            print(f"          [2] anything matching '{sub}'")
            scope = "sub" if input("  Choose [1]: ").strip() == "2" else "tertiary"
        key = save_approval(config, cmd, scope, level)
        print(f"✓ Permitted at {scope} level '{key}' for level {level}+")
        print(f"$ {_display(cmd)}")
        os.system(cmd)
        log_usage(config, cmd)
    elif confirm in ("never", "x"):
        # The human makes 'no' known — it goes on the block list, where it is
        # matched broadly, not into a mirror of the allow list.
        log_decision(config, cmd, "rejected")
        entry = save_block(config, cmd)
        print(f"✗ {strings.load(config)['blocked_now']}: '{entry}'")
        print(f"   To undo, remove it from 'blocklist' in {CONFIG_FILE}")
    else:
        print(strings.load(config)["cancelled"] + ".")
        log_decision(config, cmd, "rejected")


def show_dashboard(data: dict) -> None:
    alerts = data.get("alerts", [])
    res = data.get("resources", {})
    color = "🟢" if not any(a["level"] == "warn" for a in alerts) else "🟡"
    if any(a["level"] == "crit" for a in alerts):
        color = "🔴"
    print(f"\n{color} System Status")
    print(f"   Disk: {res.get('disk_root_percent', '?')}%  Mem: {res.get('memory_percent', '?')}%  Load: {res.get('load', '?')}")
    if alerts:
        print("   Alerts:")
        for a in alerts[:3]:
            print(f"     [{a['level']}] {a['message']}")
    print()


def main():
    config = load_config()
    level = config.get("level", 2)
    model = config.get("model", "qwen2.5-coder:7b")
    budget = config.get("budget", 4000)
    language = config.get("language", "auto")

    if needs_ollama(config) and not check_ollama():
        print("⚠️  A slot is configured for local Ollama, but ollama was not found.")
        print("   Install it from https://ollama.com, or point the slot at an API")
        print("   endpoint in ~/.config/sysmind/config.json")
        sys.exit(1)

    # Ensure fresh atlas
    if not ATLAS_FILE.exists():
        print("First scan running...")
        data = scan(config.get("paranoia", "medium"))
        with open(ATLAS_FILE, "w") as f:
            json.dump(data, f, indent=2)

    with open(ATLAS_FILE) as f:
        atlas = json.load(f)

    if level == 4:
        show_dashboard(atlas)
        print("Level 4 (Full Auto) — system is self-managing.")
        print("Open menu anytime with: sysmind")
        return

    print(MENU)
    choice = input("Choice: ").strip().lower()

    if choice == "q":
        return

    if choice in MENU_MAP:
        query = MENU_MAP[choice]
    elif choice == "6":
        query = input("What do you want to know? ").strip()
    else:
        print("Invalid choice.")
        return

    print("Building context...")
    context = build_context(query, atlas, budget)

    print("Thinking...")
    turn = run_turn(config, query, context)
    response = turn.response
    print("\n" + "=" * 50)
    print(response)
    print("=" * 50 + "\n")

    # Extract and optionally execute shell commands. Each fenced block is ONE
    # command, however many lines it spans.
    if level >= 2:
        for cmd in extract_command_blocks(response):
            execute_with_confirm(cmd, level, config, turn.asks.get(cmd))


if __name__ == "__main__":
    main()
