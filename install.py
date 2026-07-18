"""Choice-driven setup wizard for sysmind."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.resolve()
BIN_DIR = Path.home() / ".local" / "bin"
import sysmind_platform as _plat

CONFIG_DIR = Path.home().joinpath(*_plat.CURRENT.config_dir)
DATA_DIR = Path.home().joinpath(*_plat.CURRENT.data_dir)
LIB_DIR = DATA_DIR / "lib"

# Installed by pip? Then the modules are already importable and the console
# scripts already exist, so the wizard only needs to write config.
PIP_INSTALLED = any(part in str(PROJECT_ROOT)
                    for part in ("site-packages", "dist-packages"))

# Curated conscious-slot families. Only this list varies with language: the
# unconscious slot writes shell, and shell is the same in every language.
#
# Language support comes from the model, not from the interface. A family that
# covers 119 languages supports 119 languages here; nothing is special-cased.
CONSCIOUS_FAMILIES = [
    {
        "key": "qwen3",
        "label": "Qwen3 - 119 languages, Apache-2.0  [recommended]",
        "coverage": "almost any language, including Urdu, Arabic, Hindi, Bengali",
        "excludes": [],
        "sizes": {"4gb": "qwen3:1.7b", "8gb": "qwen3:8b",
                  "16gb": "qwen3:14b", "32gb": "qwen3:32b"},
    },
    {
        "key": "gemma3",
        "label": "Gemma 3 - 55 languages, strong translation quality",
        "coverage": "55 major languages",
        "excludes": [],
        "sizes": {"4gb": "gemma3:1b", "8gb": "gemma3:4b",
                  "16gb": "gemma3:12b", "32gb": "gemma3:27b"},
    },
    {
        "key": "command-r",
        "label": "Command-R - 10 major languages, long context",
        "coverage": "English, French, Spanish, Italian, German, Portuguese, "
                    "Japanese, Korean, Arabic, Chinese",
        "excludes": ["urdu", "hindi", "bengali", "swahili", "punjabi"],
        "sizes": {"4gb": "command-r7b", "8gb": "command-r7b",
                  "16gb": "command-r", "32gb": "command-r"},
    },
    {
        "key": "aya-expanse",
        "label": "Aya Expanse - 23 languages, CC-BY-NC (non-commercial)",
        "coverage": "23 languages; strong Arabic, Persian, Hindi, Turkish",
        "excludes": ["urdu", "bengali", "swahili", "punjabi"],
        "sizes": {"4gb": "aya-expanse:8b", "8gb": "aya-expanse:8b",
                  "16gb": "aya-expanse:8b", "32gb": "aya-expanse:32b"},
    },
]

# The unconscious slot. Language-agnostic by definition.
CODER_SIZES = {
    "4gb": "qwen2.5-coder:1.5b", "8gb": "qwen2.5-coder:7b",
    "16gb": "qwen2.5-coder:14b", "32gb": "qwen3-coder:30b",
}

import sysmind_i18n as i18n

SCRIPTS = ["sysmind.py", "sysmind_scan.py", "sysmind_orbit.py", "sysmind_display.py",
           "sysmind_common.py", "sysmind_doctor.py", "sysmind_partners.py",
           "sysmind_sync.py", "sysmind_turn.py", "sysmind_strings.py",
           "sysmind_i18n.py", "sysmind_platform.py",
           "sysmind_scan_linux.py", "sysmind_scan_windows.py"]


_CHOICE = ["Choice", "default"]   # localised once the language is known


def ask(question: str, options: list, default: int = 0) -> int:
    print(f"\n{question}")
    for i, opt in enumerate(options):
        marker = f" ({_CHOICE[1]})" if i == default else ""
        print(f"  [{i+1}] {opt}{marker}")
    ans = input(f"{_CHOICE[0]}: ").strip()
    if not ans:
        return default
    try:
        idx = int(ans) - 1
        if 0 <= idx < len(options):
            return idx
    except ValueError:
        pass
    print("Invalid choice, using default.")
    return default


def ask_text(question: str, default: str = "") -> str:
    """Free-text answer. Used where a fixed list would exclude people."""
    print(f"\n{question}")
    if default:
        print(f"  (press enter for: {default})")
    return input("> ").strip() or default


def detect_ram() -> str:
    if sys.platform.startswith("win"):
        # No /proc. GlobalMemoryStatusEx via ctypes, no dependencies.
        try:
            import ctypes

            class _MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong),
                            ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            st = _MS()
            st.dwLength = ctypes.sizeof(_MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
            gb = st.ullTotalPhys / (1024 ** 3)
            if gb < 5:
                return "4gb"
            if gb < 12:
                return "8gb"
            if gb < 24:
                return "16gb"
            return "32gb"
        except Exception:
            return "8gb"
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal"):
                    kb = int(line.split()[1])
                    gb = kb / 1024 / 1024
                    if gb < 5:
                        return "4gb"
                    elif gb < 12:
                        return "8gb"
                    elif gb < 24:
                        return "16gb"
                    else:
                        return "32gb"
    except Exception:
        pass
    return "8gb"


def create_wrapper(name: str, script_name: str) -> None:
    """Create a shell wrapper in ~/.local/bin/ that calls the Python script."""
    wrapper = BIN_DIR / name
    content = f'''#!/bin/bash
export PYTHONPATH="{LIB_DIR}:$PYTHONPATH"
exec python3 "{LIB_DIR / script_name}" "$@"
'''
    with open(wrapper, "w") as f:
        f.write(content)
    os.chmod(wrapper, 0o755)
    print(f"Installed wrapper: {wrapper}")


def install():
    print("🧠 Sysmind")
    print("=" * 40)

    # Asked first, and shown in each language's own script, so answering it
    # requires no English. Everything after this is in the chosen language.
    print("\nLanguage / زبان / اللغة / भाषा / 语言")
    for i, (_, endonym, english) in enumerate(i18n.LANGUAGES):
        label = endonym if endonym == english else f"{endonym}  ({english})"
        print(f"  [{i+1}] {label}")
    print(f"  [{len(i18n.LANGUAGES)+1}] Other - type the name")
    raw = input("Choice [1]: ").strip()

    try:
        pick = int(raw) - 1 if raw else 0
    except ValueError:
        pick = 0

    if 0 <= pick < len(i18n.LANGUAGES):
        code, _, language = i18n.LANGUAGES[pick]
    else:
        # Any language at all: the wizard falls back to English, but the
        # runtime still speaks whatever they name.
        language = ask_text("Language?", "auto") or "auto"
        code = language.strip().lower()
    if code == "english":
        language = "auto"

    T = lambda key: i18n.t(code, key)
    _CHOICE[0], _CHOICE[1] = T("choice"), T("default")
    print("\n" + T("title"))
    print("=" * 40)

    level_idx = ask(T("q_level"), [T("level1"), T("level2"),
                                   T("level3"), T("level4")], default=1)
    level = level_idx + 1

    ram = detect_ram()
    tiers = ["4gb", "8gb", "16gb", "32gb"]
    ram_options = [T("ram1"), T("ram2"), T("ram3"), T("ram4")]
    ram_idx = ask(T("q_ram"), ram_options,
                  default=tiers.index(ram) if ram in tiers else 1)
    ram_tier = tiers[ram_idx]
    budgets = {"4gb": 2000, "8gb": 4000, "16gb": 8000, "32gb": 8000}

    # Conscious slot: the choice that decides which languages work.
    fam_idx = ask(T("q_model"),
                  [f["label"] for f in CONSCIOUS_FAMILIES], default=0)
    family = CONSCIOUS_FAMILIES[fam_idx]

    if language.strip().lower() in family["excludes"]:
        print(f"\n  !! {family['key']} does not cover {language}.")
        print(f"     It covers: {family['coverage']}")
        print("     Qwen3 covers 119 languages including this one - using it instead.")
        family = CONSCIOUS_FAMILIES[0]

    conscious_model = family["sizes"][ram_tier]
    unconscious_model = CODER_SIZES[ram_tier]

    print(f"\n  {conscious_model:24} {T('conscious_slot')}")
    print(f"  {unconscious_model:24} {T('unconscious_slot')}")
    if ram_tier in ("4gb", "8gb"):
        print("  note: two models on this much RAM is tight. Ollama swaps them")
        print("        on demand, or point one slot at an API endpoint in")
        print("        config.json (backend: openai, base_url, api_key_env).")

    paranoia_idx = ask(T("q_paranoia"), [T("low"), T("medium"), T("high")], default=1)
    paranoia = ["low", "medium", "high"][paranoia_idx]

    notif_idx = ask(T("q_notify"), [T("notify1"), T("notify2"), T("notify3")], default=0)
    notifications = ["native", "terminal", "none"][notif_idx]

    launch_idx = ask(T("q_launch"), [T("launch1"), T("launch2"), T("launch3")], default=0)
    launch = ["alias", "hotkey", "both"][launch_idx]

    blocklist_idx = ask(T("q_block"), [T("block_yes"), T("block_no")], default=0)
    blocklist_enabled = blocklist_idx == 0

    config = {
        "level": level,
        "ram_tier": ram_tier,
        "slots": {
            "conscious": {"backend": "ollama", "model": conscious_model},
            "unconscious": {"backend": "ollama", "model": unconscious_model},
        },
        "model": conscious_model,   # legacy key, kept for older readers
        "budget": budgets[ram_tier],
        "language": language,
        "paranoia": paranoia,
        "notifications": notifications,
        "launch_method": launch,
        "display_dashboard": level >= 3,
        "blocklist_enabled": blocklist_enabled,
    }

    # Create dirs
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not PIP_INSTALLED:
        LIB_DIR.mkdir(parents=True, exist_ok=True)
        BIN_DIR.mkdir(parents=True, exist_ok=True)

    # Write config
    with open(CONFIG_DIR / "config.json", "w") as f:
        json.dump(config, f, indent=2)

    if not PIP_INSTALLED and not sys.platform.startswith("win"):
        # Source-tree install on Unix: copy the modules somewhere stable and
        # make shell wrappers. Under pip the package manager does both, and on
        # Windows a #!/bin/bash wrapper is meaningless - use pip there.
        for script in SCRIPTS:
            src = PROJECT_ROOT / script
            if src.exists():
                dst = LIB_DIR / script
                shutil.copy2(src, dst)
                print(f"Installed: {dst}")

        create_wrapper("sysmind", "sysmind.py")
        create_wrapper("sysmind-scan", "sysmind_scan.py")
        create_wrapper("sysmind-orbit", "sysmind_orbit.py")
        create_wrapper("sysmind-display", "sysmind_display.py")
        create_wrapper("sysmind-doctor", "sysmind_doctor.py")
        create_wrapper("sysmind-sync", "sysmind_sync.py")

    # Shell alias / PATH
    shell_rc = None
    shell = os.environ.get("SHELL", "")
    if "bash" in shell:
        shell_rc = Path.home() / ".bashrc"
    elif "zsh" in shell:
        shell_rc = Path.home() / ".zshrc"

    if shell_rc and shell_rc.exists() and not PIP_INSTALLED:
        path_line = 'export PATH="$HOME/.local/bin:$PATH"\n'
        with open(shell_rc, "r") as f:
            existing = f.read()
        if path_line.strip() not in existing:
            with open(shell_rc, "a") as f:
                f.write(f"\n# sysmind\n{path_line}")
            print(f"Added PATH to {shell_rc}")

    # Cron for Level 3-4
    if level >= 3:
        cron_line = f"0 * * * * {BIN_DIR}/sysmind-scan {paranoia} > /dev/null 2>&1\n"
        try:
            r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
            existing = r.stdout if r.returncode == 0 else ""
            if "sysmind-scan" not in existing:
                new_cron = existing + cron_line
                subprocess.run(["crontab", "-"], input=new_cron, text=True, check=True)
                print("Added hourly scan to crontab.")
        except Exception as e:
            print(f"Could not set up cron: {e}")

    # Check/install Ollama
    ollama_found = shutil.which("ollama") is not None
    if not ollama_found:
        print("\n⚠️  Ollama not found.")
        print("Install it from: https://ollama.com/download/linux")
        print(f"Then: ollama pull {conscious_model} && ollama pull {unconscious_model}")
    else:
        for m in (conscious_model, unconscious_model):
            print(f"\nPulling {m} ...")
            subprocess.run(["ollama", "pull", m])

    print("\n" + "=" * 40)
    print(T("done"))
    print(f"Config: {CONFIG_DIR / 'config.json'}")
    print(f"Scripts: {LIB_DIR}")
    print(f"Wrappers: {BIN_DIR}")
    # The wizard itself cannot be localised — no model exists until now. But
    # the interface can be, the moment the models are on disk, so the first
    # real run is already in the user's language without them having to know
    # that a localisation step exists.
    if language.strip().lower() not in ("auto", "english") and ollama_found:
        print(f"\n{T('localising')}")
        try:
            if not PIP_INSTALLED:
                sys.path.insert(0, str(LIB_DIR))
            import sysmind_strings as strings
            from sysmind_partners import partner_from_slot

            rendered = strings.localise(
                partner_from_slot(config["slots"]["conscious"]), language)
            done = sum(1 for k, v in rendered.items() if v != strings.BASE[k])

            # Only cache a localisation that actually happened. Storing an
            # all-English result under the user's language would claim a
            # translation exists and hide the failure.
            if done:
                config["ui_strings"] = {"language": language, "strings": rendered}
                with open(CONFIG_DIR / "config.json", "w") as f:
                    json.dump(config, f, indent=2)
                print(f"  {done}/{len(strings.BASE)} strings now in {language}.")
            else:
                print("  The model returned nothing usable — interface stays")
                print("  English. Retry later with: sysmind-sync localise")
        except Exception as e:
            print(f"  Could not localise now ({e}).")
            print(f"  Retry later with: sysmind-sync localise")

    print("\n" + T("calibrate_hint"))
    if PIP_INSTALLED:
        print("\nRun: sysmind")
    elif shell_rc:
        print(f"\nRun: source {shell_rc} && sysmind")
    else:
        print(f"\nRun: export PATH=\"$HOME/.local/bin:$PATH\" && sysmind")


if __name__ == "__main__":
    install()
