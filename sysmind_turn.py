"""The two-slot turn: conscious composes, unconscious codes, conscious explains.

The shell block in the returned response is assembled HERE, from the coder's
output verbatim. The conscious partner never emits it — it only writes prose
around it. That is structural, not a prompt instruction: the model that speaks
to the user cannot substitute a different command for the one that will run,
however confidently it narrates.

Flow for one turn:

    user request (any language)
        -> conscious: turn intent into a completion prompt for the coder
        -> unconscious: complete it into shell (may be a mute code model)
        -> conscious: explain the result in the user's language
        -> this module: assemble prose + the coder's command, sealed
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from sysmind_partners import LLMPartner, PartnerError, partners_from_config, slots_from_config
from sysmind_sync import SyncProfile, looks_like_command, shell_syntax_ok

_FENCE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)

# Kept short deliberately: a small local model follows one instruction better
# than five, and this one has to work for models that barely follow any.
_COMPOSE = """THE USER ASKED, IN THEIR OWN WORDS:
{query}

Background on their machine, for reference only. Do NOT answer a question
about this that they did not ask - a striking number here does not change
what they wanted to know:
{context}

Write ONE English comment line naming the shell task THE USER asked for,
exactly like:
# shell one-liner: <what to do>

Rules, in order of importance:

1. If the request is vague, ask for a command that only INSPECTS - one that
   displays, lists or counts. Never one that deletes, moves or modifies.
   The user can ask for a specific change once they have seen the state.
2. Answer the question the user actually asked. Do not substitute a
   different task.
3. Describe the task in plain words rather than writing shell yourself.
4. Be concrete about the target: name the actual path, unit or package.
5. Never use vague verbs like clean, free up, fix, tidy, sort out, or
   handle. They produce destructive commands. Say exactly what to display,
   list, count, or restart.

Output only that single line."""

_EXPLAIN = """The user asked:
{query}

This shell command was produced to do it:
{command}

Explain in {language} what it does, and note anything risky about it.
Do NOT output the command itself — it is shown to the user separately.
Explain only."""

# The unconscious cannot address the human, so its request to act is voiced by
# the conscious slot, in the human's language, about this specific command.
_ASK = """The user asked:
{query}

This command will run on their computer only if they agree:
{command}

What it does: {explanation}

Write ONE short question in {language} asking whether to run it. Name the real
effect on their system - if it deletes, installs or changes something, say so.
Do not include the command itself. Output only the question."""


def needs_ollama(config: Dict[str, Any]) -> bool:
    """True only if some slot actually uses a local Ollama backend."""
    for slot in slots_from_config(config).values():
        if str(slot.get("backend", "ollama")).lower() == "ollama":
            return True
    return False


def resolve_roles(config: Dict[str, Any],
                  partners: Optional[Tuple[LLMPartner, LLMPartner]] = None
                  ) -> Tuple[LLMPartner, LLMPartner, Optional[str]]:
    """Return (conscious, unconscious, note), honouring a calibration profile.

    Config assigns partners to slots; calibration decides which partner is
    actually better suited to which role. Calibration wins, because it measured.
    """
    conscious, unconscious = partners or partners_from_config(config)

    try:
        profile = SyncProfile.load()
    except Exception:
        return conscious, unconscious, None

    roles = {profile.partner_a.get("name"): profile.role_a,
             profile.partner_b.get("name"): profile.role_b}
    con_role = roles.get(conscious.metadata.name)
    unc_role = roles.get(unconscious.metadata.name)

    if con_role is None or unc_role is None:
        return conscious, unconscious, "calibration profile does not match the configured models — run sysmind-sync calibrate"
    if con_role == "unconscious" and unc_role == "conscious":
        return unconscious, conscious, "calibration swapped the slots"
    return conscious, unconscious, None


def compose_coder_prompt(conscious: LLMPartner, query: str, context: str,
                         timeout: float) -> str:
    """Have the conscious model write the coder's prompt.

    Completion-shaped on purpose: the unconscious partner may be a base code
    model that cannot follow an instruction, but will complete a comment header.
    """
    try:
        out = conscious.complete(
            _COMPOSE.format(context=context, query=query), timeout=timeout)
    except Exception:
        out = ""

    for line in out.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") and len(stripped) > 3:
            return stripped + "\n"

    # The conscious model failed to give a usable header; use the request itself
    # rather than inventing one.
    return "# shell one-liner: {}\n".format(" ".join(query.split()))


def extract_command(text: str) -> Optional[str]:
    """Pull a runnable command out of the coder's completion.

    Accepts fenced or bare output — a model that obeyed 'code only' emits no
    fences — but only if it both reads as a command and parses as shell.
    """
    candidates: List[str] = [b.strip() for b in _FENCE.findall(text) if b.strip()]
    if not candidates and text.strip():
        candidates = [text.strip()]

    for cand in candidates:
        # Drop any leading comment lines the model echoed back.
        body = "\n".join(ln for ln in cand.splitlines()
                         if not ln.strip().startswith("#")).strip()
        if body and looks_like_command(body) and shell_syntax_ok(body) is not False:
            return body
    return None


def sanitise_prose(text: str) -> str:
    """Strip every fence from model prose before it is embedded in a response.

    This is what makes the sealed payload structural instead of a polite
    request. Asking the conscious model not to emit a command is not a control:
    it can ignore the instruction, and a ```bash block in its explanation would
    go straight to the executor alongside the real one. Removing fences here
    guarantees the only fenced block in the response is the one assembled from
    the coder's output.
    """
    text = _FENCE.sub(" ", text)      # complete fenced blocks
    return text.replace("```", "").strip()   # stray / unterminated fences


def explain(conscious: LLMPartner, command: str, query: str, language: str,
            timeout: float) -> str:
    lang = ("the same language the user wrote in"
            if (language or "auto").lower() == "auto" else language)
    try:
        return sanitise_prose(conscious.complete(
            _EXPLAIN.format(query=query, command=command, language=lang),
            timeout=timeout))
    except Exception as e:
        return "(could not reach the conscious model to explain: {})".format(e)


def compose_ask(conscious: LLMPartner, command: str, explanation: str,
                query: str, language: str, timeout: float) -> Optional[str]:
    """The conscious slot voices the unconscious slot's request for permission.

    Returns None on any failure, so the caller falls back to the cached generic
    prompt rather than losing the ability to ask at all.
    """
    lang = ("the same language the user wrote in"
            if (language or "auto").lower() == "auto" else language)
    try:
        asked = sanitise_prose(conscious.complete(
            _ASK.format(query=query, command=command, explanation=explanation,
                        language=lang),
            timeout=timeout))
    except Exception:
        return None
    asked = " ".join(asked.split())
    return asked[:300] if asked else None


class Turn:
    """One turn's output: what to show, and how to ask about each command."""

    def __init__(self, response: str, asks: Optional[Dict[str, str]] = None):
        self.response = response
        self.asks = asks or {}


def run_turn(config: Dict[str, Any], query: str, context: str,
             partners: Optional[Tuple[LLMPartner, LLMPartner]] = None,
             timeout: float = 90.0) -> "Turn":
    """Run one full turn.

    Returns the response text the extractor parses, plus the conscious slot's
    permission question for each command it contains.
    """
    conscious, unconscious, note = resolve_roles(config, partners)
    language = config.get("language", "auto")
    prefix = "note: {}\n\n".format(note) if note else ""

    header = compose_coder_prompt(conscious, query, context, timeout)

    try:
        completion = unconscious.complete(header, timeout=timeout)
    except PartnerError as e:
        return Turn(prefix + "The unconscious slot ({}) could not be reached: {}".format(
            unconscious.metadata.name, e))
    except Exception as e:
        return Turn(prefix + "The unconscious slot failed: {}".format(e))

    command = extract_command(completion)

    if command is None:
        # Nothing runnable came back. Say so plainly rather than dressing up a
        # non-answer as one — and offer nothing to the executor.
        return Turn(prefix + (
            "The unconscious slot ({}) did not return a runnable command for:\n"
            "  {}\n\nIt replied:\n{}".format(
                unconscious.metadata.name, header.strip(),
                sanitise_prose(completion)[:600])))

    explanation = explain(conscious, command, query, language, timeout)
    asked = compose_ask(conscious, command, explanation, query, language, timeout)

    # The command is inserted here, verbatim, by code. The conscious model does
    # not get to write this block.
    response = "{}{}\n\n```bash\n{}\n```".format(prefix, explanation, command)
    return Turn(response, {command: asked} if asked else {})
