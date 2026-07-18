"""Interface strings, rendered once by the conscious model, then frozen.

The prompts a person answers were English-only because they are hardcoded
`input()` text — the one part of the system the language model never touched.
This hands that text to the model that already speaks the user's language.

Two rules make it safe to let a model produce interface text:

1. **It is generated once and cached**, at install or calibration time — never
   per prompt. So the prompt is deterministic at runtime, costs nothing, and
   still works when the model is unreachable.

2. **The model never reads the answer.** Options are numbered, and digits mean
   the same thing in every language. The digit -> action mapping lives in code.
   A mistranslated label can confuse; it cannot execute the wrong thing. The
   English keyword is kept alongside every option as an anchor.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

# Canonical English. The source of truth, and the fallback whenever a
# localisation is missing, stale, or unparseable.
BASE: Dict[str, str] = {
    "ask_question": "Run this command?",
    "opt_once": "run it this time",
    "opt_no": "do not run it",
    "opt_always": "always permit this",
    "opt_never": "never permit this",
    "choose": "Choose",
    "history": "history",
    "approved_times": "approved",
    "rejected_times": "rejected",
    "never_asked": "never asked about this before",
    "refused_before": "You previously refused this",
    "blocked_seed": "This matches the built-in block list",
    "invalid_shell": "That is not valid shell — skipped",
    "auto_approved": "Permitted earlier, running it",
    "cancelled": "Cancelled",
    "permitted_now": "Permitted from now on",
    "blocked_now": "Added to your block list",
    "to_undo": "To undo, edit",
    "scope_exact": "only this exact command",
    "scope_family": "anything matching",
}

# What the user types. Digits, because they are the same in every language.
KEYS = {"1": "once", "2": "no", "3": "always", "4": "never"}

_PROMPT = """Translate each value in this JSON object into {language}.

Rules:
- Keep every key exactly as it is. Return only the JSON object.
- These are buttons and prompts in a system administration tool. The user
  reads them to decide whether to run a command on their computer, so be
  plain and unambiguous rather than literary.
- "always permit this" and "never permit this" are permanent decisions.
  Make that permanence clear.

{payload}"""


def localise(partner, language: str, timeout: float = 120.0) -> Dict[str, str]:
    """Ask the conscious model to render BASE into `language`.

    Returns a dict that always has every key: anything the model omits or
    mangles falls back to English rather than going missing.
    """
    if not language or language.strip().lower() in ("auto", "english"):
        return dict(BASE)

    try:
        raw = partner.complete(
            _PROMPT.format(language=language,
                           payload=json.dumps(BASE, ensure_ascii=False, indent=2)),
            timeout=timeout)
    except Exception:
        return dict(BASE)

    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        return dict(BASE)

    try:
        got = json.loads(raw[start:end + 1])
    except (ValueError, TypeError):
        return dict(BASE)

    out = dict(BASE)
    for key in BASE:
        value = got.get(key)
        if isinstance(value, str) and value.strip():
            out[key] = value.strip()
    return out


def load(config: Dict[str, Any]) -> Dict[str, str]:
    """Cached strings for the configured language, English if none apply."""
    cache = config.get("ui_strings")
    language = (config.get("language") or "auto").strip().lower()

    if (isinstance(cache, dict)
            and str(cache.get("language", "")).strip().lower() == language
            and isinstance(cache.get("strings"), dict)):
        out = dict(BASE)
        out.update({k: v for k, v in cache["strings"].items()
                    if k in BASE and isinstance(v, str) and v.strip()})
        return out
    return dict(BASE)


def save(config: Dict[str, Any], language: str, strings: Dict[str, str]) -> None:
    config["ui_strings"] = {"language": language, "strings": strings}


def option_line(strings: Dict[str, str], key: str, label_key: str) -> str:
    """One numbered option, with the English keyword kept as an anchor.

    The anchor matters: if a translation is poor, there is still something
    stable to recognise on the line that decides whether a command runs.
    """
    english = BASE[label_key]
    localised = strings.get(label_key, english)
    if localised == english:
        return "  [{}] {}".format(key, english)
    return "  [{}] {}  ({})".format(key, localised, english)
