"""Sync calibration for sysmind — the conscious/unconscious handshake.

Ported from creature's SyncHandshake (Sources/CreatureSpine/Sync/), with the
scoring rebuilt. The idea that carries over unchanged:

    Both partners receive the SAME prompt at the SAME moment. What tells them
    apart is not *who answered* but *in what basis they answered* — the
    conscious speaks in words, the unconscious speaks in code. Neither model
    is told which it is.

What deliberately did NOT carry over:

  * `hasExplanation` was an English keyword sniff ("because"/"therefore"/...),
    which scores a fluent Urdu answer as if it explained nothing. Replaced with
    script- and structure-based signals that work in any language.
  * `hasCodeBlocks` was a ``` fence sniff, so a model obeying "reply with only
    the code" scored *lower* than one that ignored the instruction. Replaced
    with `bash -n` verification: unfenced but valid shell counts as code.
  * Role assignment compared one axis only and could pick the worse pairing.
    Replaced with the 2x2 joint assignment.
  * Confidence was max() across both partners regardless of who held the role,
    so a pair could report IN SYNC while the code slot was incompetent.
    Confidence is now read from the partner that actually holds the role.
"""
from __future__ import annotations

import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from sysmind_common import DATA_DIR, ensure_dirs, load_config
from sysmind_partners import (LLMPartner, PartnerError, PartnerRole,
                              partners_from_config)

PROFILE_FILE = DATA_DIR / "sync_profile.json"

# --- language-agnostic response analysis -------------------------------------

SCRIPT_RANGES: Dict[str, Tuple[Tuple[int, int], ...]] = {
    "latin": ((0x41, 0x5A), (0x61, 0x7A), (0xC0, 0x24F)),
    "arabic": ((0x600, 0x6FF), (0x750, 0x77F), (0x8A0, 0x8FF),
               (0xFB50, 0xFDFF), (0xFE70, 0xFEFF)),
    "devanagari": ((0x900, 0x97F),),
    "cyrillic": ((0x400, 0x4FF),),
    "greek": ((0x370, 0x3FF),),
    "hebrew": ((0x590, 0x5FF),),
    "han": ((0x3400, 0x4DBF), (0x4E00, 0x9FFF)),
    "kana": ((0x3040, 0x30FF),),
    "hangul": ((0x1100, 0x11FF), (0xAC00, 0xD7AF)),
    "thai": ((0xE00, 0xE7F),),
    "bengali": ((0x980, 0x9FF),),
    "tamil": ((0xB80, 0xBFF),),
}

# Which script a reply in this language should be written in.
LANGUAGE_SCRIPT: Dict[str, str] = {
    "english": "latin", "spanish": "latin", "french": "latin",
    "german": "latin", "portuguese": "latin", "indonesian": "latin",
    "urdu": "arabic", "arabic": "arabic", "persian": "arabic",
    "farsi": "arabic", "pashto": "arabic", "sindhi": "arabic",
    "hindi": "devanagari", "marathi": "devanagari", "nepali": "devanagari",
    "russian": "cyrillic", "ukrainian": "cyrillic", "bulgarian": "cyrillic",
    "greek": "greek", "hebrew": "hebrew", "thai": "thai",
    "bengali": "bengali", "tamil": "tamil",
    "chinese": "han", "mandarin": "han", "japanese": "kana", "korean": "hangul",
}

# Sentence terminators across scripts (Urdu '۔', Arabic '؟', CJK '。' included).
_SENTENCE_ENDS = ".!?۔؟。！？।"
_FENCE_RE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)


def script_of_language(language: str) -> Optional[str]:
    return LANGUAGE_SCRIPT.get((language or "").strip().lower())


def script_ratio(text: str, script: str) -> float:
    """Fraction of *letters* that belong to `script`. Digits/punctuation ignored."""
    ranges = SCRIPT_RANGES.get(script)
    if not ranges:
        return 0.0
    letters = 0
    hits = 0
    for ch in text:
        if not ch.isalpha():
            continue
        letters += 1
        cp = ord(ch)
        if any(lo <= cp <= hi for lo, hi in ranges):
            hits += 1
    return hits / letters if letters else 0.0


def shell_syntax_ok(code: str, timeout: float = 5.0) -> Optional[bool]:
    """Parse-check shell with `bash -n`. Returns None if bash is unavailable.

    `bash -n` reads and parses without executing — safe to run on model output.
    NOTE: this is necessary but nowhere near sufficient. Almost any prose parses
    as shell ("hmm, not sure" is the command `hmm,` with two arguments), so it
    must be paired with looks_like_command().
    """
    if not code.strip():
        return None
    try:
        r = subprocess.run(["bash", "-n"], input=code, text=True,
                           capture_output=True, timeout=timeout)
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None


_SHELL_KEYWORDS = {"for", "while", "until", "if", "case", "do", "done",
                   "then", "fi", "esac", "function"}

_KNOWN_COMMANDS = {
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
}

_CMD_NAME_RE = re.compile(r"^(?:/[\w./+-]+|[A-Za-z_][\w.+-]*)$")
_SHELL_SIGNAL_RE = re.compile(r"(?:^|\s)-{1,2}\w|[|>]|\$\(|`|/\w")


def looks_like_command(text: str, strict: bool = False) -> bool:
    """True if this reads as shell rather than prose that merely parses.

    This is the check `bash -n` cannot make. Without it, a partner that replies
    "hmm, not sure" to a shell task scores as a perfect coder.

    `strict` requires a recognised command head rather than accepting any weak
    shell signal. Use it when asking "is this prose actually code?", where a
    sentence mentioning /var/log must not be mistaken for a command.
    """
    stripped = text.strip()
    if not stripped:
        return False
    lines = [ln.strip() for ln in stripped.splitlines()
             if ln.strip() and not ln.strip().startswith("#")]
    if not lines:
        return False
    tokens = lines[0].split()
    if not tokens:
        return False

    head = tokens[0]
    if head in ("sudo", "env") and len(tokens) > 1:
        head = tokens[1]
    if head in _SHELL_KEYWORDS:
        return True
    if not _CMD_NAME_RE.match(head):
        return False          # "hmm," / "I'll" / anything punctuation-laden
    if head in _KNOWN_COMMANDS or head.startswith("/"):
        return True
    if strict:
        return False
    # Unknown command name: require some other shell signal to believe it.
    return bool(_SHELL_SIGNAL_RE.search(stripped))


@dataclass
class Fingerprint:
    """What a single response looks like, without assuming a language."""
    content: str
    latency_ms: float
    code_blocks: List[str] = field(default_factory=list)
    prose: str = ""
    has_code: bool = False
    code_valid: Optional[bool] = None
    prose_chars: int = 0
    sentence_count: int = 0
    code_char_ratio: float = 0.0
    prose_is_code: bool = False

    @classmethod
    def analyse(cls, content: str, latency_ms: float,
                verify_shell: bool = False) -> "Fingerprint":
        blocks = [b.strip() for b in _FENCE_RE.findall(content) if b.strip()]
        prose = _FENCE_RE.sub(" ", content).strip()

        code_valid: Optional[bool] = None
        has_code = bool(blocks)
        if verify_shell:
            if blocks:
                joined = "\n".join(blocks)
                if looks_like_command(joined):
                    code_valid = shell_syntax_ok(joined)
                else:
                    has_code = False      # a fenced block of prose is not code
                    code_valid = False
            else:
                # No fences. A model that obeyed "reply with the command only"
                # is not penalised: bare text that both reads and parses as
                # shell counts as code.
                if looks_like_command(content):
                    code_valid = shell_syntax_ok(content)
                    if code_valid is not False:
                        has_code = True
                        blocks = [content.strip()]
                        prose = ""
                else:
                    code_valid = False

        code_chars = sum(len(b) for b in blocks)
        total = max(len(content), 1)
        sentences = sum(1 for ch in prose if ch in _SENTENCE_ENDS)

        # A code-only model answering a reasoning probe emits a bare command
        # with no fences. Without this it would be counted as 'prose' and could
        # win the conscious slot it cannot possibly fill.
        prose_is_code = (bool(prose.strip()) and sentences <= 1
                         and len(prose.strip()) <= 400
                         and looks_like_command(prose, strict=True))

        return cls(content=content, latency_ms=latency_ms, code_blocks=blocks,
                   prose=prose, has_code=has_code, code_valid=code_valid,
                   prose_chars=len(prose), sentence_count=sentences,
                   code_char_ratio=code_chars / total,
                   prose_is_code=prose_is_code)


# --- tests -------------------------------------------------------------------

@dataclass
class SyncTest:
    id: str
    name: str
    prompt: str
    role: PartnerRole            # the role this test actually scores
    system: Optional[str] = None
    weight: float = 1.0
    expect_script: Optional[str] = None   # language probe
    expect_shell: bool = False            # verify output with `bash -n`


def standard_battery(language: str = "auto") -> List[SyncTest]:
    """Balanced sysadmin battery: equal weight per role, shell-native content.

    creature's battery was 6 conscious tests to 3 unconscious, all of it Swift
    and PostgreSQL. This is 3-and-3 on Linux operations, plus an optional
    language probe when the user has pinned a non-English reply language.
    """
    conscious = [
        SyncTest("r1", "Triage Reasoning",
                 "The root partition on a Debian laptop is 94% full. Explain how "
                 "you would work out what is consuming it and what is safe to "
                 "remove. Do not give commands, explain the approach.",
                 PartnerRole.CONSCIOUS, weight=1.0),
        SyncTest("r2", "Trade-off Analysis",
                 "Compare running a periodic task as a systemd timer versus a "
                 "cron job on a personal Linux machine. What are the practical "
                 "consequences of each?",
                 PartnerRole.CONSCIOUS, weight=1.0),
        SyncTest("r3", "Failure Explanation",
                 "A systemd service fails on boot but starts fine manually "
                 "afterwards. Explain the likely causes and how you would "
                 "narrow them down.",
                 PartnerRole.CONSCIOUS, weight=1.0),
    ]
    # Completion-shaped, deliberately NOT instruction-shaped. A base code model
    # with no instruction tuning cannot obey "reply with the command only", but
    # it will complete a comment header natively. Instruct models handle this
    # form too, so one battery measures both without favouring either.
    unconscious = [
        SyncTest("c1", "Disk Report",
                 "# shell one-liner: print the ten largest directories under "
                 "/var, human readable, largest first\n",
                 PartnerRole.UNCONSCIOUS, weight=1.0, expect_shell=True),
        SyncTest("c2", "Service Query",
                 "# shell one-liner: show every systemd unit in a failed state, "
                 "no pager, one per line\n",
                 PartnerRole.UNCONSCIOUS, weight=1.0, expect_shell=True),
        SyncTest("c3", "Package State",
                 "# shell one-liner: list packages with available upgrades, "
                 "then count them\n",
                 PartnerRole.UNCONSCIOUS, weight=1.0, expect_shell=True),
    ]

    tests = conscious + unconscious

    script = script_of_language(language)
    if script and script != "latin":
        tests.append(SyncTest(
            "l1", "Language Fluency",
            "Explain in {} what a system package upgrade does and why it "
            "matters. Write only prose.".format(language),
            PartnerRole.CONSCIOUS, weight=1.5, expect_script=script))
    return tests


# --- scoring -----------------------------------------------------------------

def score_response(test: SyncTest, fp: Fingerprint) -> float:
    """Score one response for the role its test measures. Range 0..1.

    Unlike creature's version there is no 0.5 floor — an empty or failed
    response scores near zero rather than half marks.
    """
    if not fp.content.strip():
        return 0.0

    if test.role == PartnerRole.CONSCIOUS:
        score = 0.0
        if not fp.prose_is_code:
            # Substantive prose, measured in characters rather than keywords.
            score += 0.4 * min(fp.prose_chars / 300.0, 1.0)
            # The conscious slot must not answer in code.
            score += 0.2 if fp.code_char_ratio < 0.5 else 0.0
        # Structured into sentences (terminators are multi-script).
        score += 0.2 if fp.sentence_count >= 2 else 0.0
        # Language probe: does it actually reply in the user's script?
        if test.expect_script:
            score += 0.2 if script_ratio(fp.prose, test.expect_script) >= 0.5 else 0.0
        else:
            score += 0.2  # neutral when no language is pinned
        expected_ms = 2000.0
    else:
        score = 0.0
        score += 0.3 if fp.has_code else 0.0
        # Substance, not shape: does it actually parse as shell?
        if fp.code_valid is True:
            score += 0.5
        elif fp.code_valid is None:
            score += 0.25   # bash unavailable — cannot verify, do not punish
        # Obeying "command only" is rewarded, not penalised.
        score += 0.2 if fp.code_char_ratio >= 0.5 or fp.prose_chars < 80 else 0.0
        expected_ms = 1500.0

    if fp.latency_ms > expected_ms * 2:
        score -= 0.15
    return max(0.0, min(1.0, score))


# --- handshake ---------------------------------------------------------------

@dataclass
class SyncProfile:
    partner_a: Dict[str, Any]
    partner_b: Dict[str, Any]
    role_a: str
    role_b: str
    confidence_conscious: float
    confidence_unconscious: float
    latency_a_ms: float
    latency_b_ms: float
    in_sync: bool
    test_count: int
    prose_capability_a: float = 0.0
    prose_capability_b: float = 0.0
    notes: List[str] = field(default_factory=list)

    def holder(self, role: PartnerRole) -> Dict[str, Any]:
        """Metadata of the partner that actually holds `role`."""
        return self.partner_a if self.role_a == role.value else self.partner_b

    def confidence(self, role: PartnerRole) -> float:
        return (self.confidence_conscious if role == PartnerRole.CONSCIOUS
                else self.confidence_unconscious)

    def save(self, path=PROFILE_FILE) -> None:
        ensure_dirs()
        with open(path, "w") as f:
            json.dump(self.__dict__, f, indent=2)

    @classmethod
    def load(cls, path=PROFILE_FILE) -> "SyncProfile":
        with open(path) as f:
            return cls(**json.load(f))

    @property
    def summary(self) -> str:
        lines = ["Sync Profile", "=" * 40, ""]
        lines.append("A ({}): {}".format(self.role_a, self.partner_a.get("name")))
        lines.append("B ({}): {}".format(self.role_b, self.partner_b.get("name")))
        lines.append("")
        lines.append("Confidence")
        lines.append("  conscious   {:.0f}%  ({})".format(
            self.confidence_conscious * 100,
            self.holder(PartnerRole.CONSCIOUS).get("name")))
        lines.append("  unconscious {:.0f}%  ({})".format(
            self.confidence_unconscious * 100,
            self.holder(PartnerRole.UNCONSCIOUS).get("name")))
        lines.append("")
        lines.append("Language A {:.0f}%   B {:.0f}%   (share of replies that "
                     "were prose, not code)".format(
                         self.prose_capability_a * 100,
                         self.prose_capability_b * 100))
        lines.append("Latency  A {:.0f}ms   B {:.0f}ms".format(
            self.latency_a_ms, self.latency_b_ms))
        lines.append("Status   {}".format("IN SYNC" if self.in_sync else "NEEDS ATTENTION"))
        for n in self.notes:
            lines.append("  ! " + n)
        return "\n".join(lines)


class SyncHandshake:
    """Calibrate two partners into conscious/unconscious roles."""

    def __init__(self, partner_a: LLMPartner, partner_b: LLMPartner,
                 tests: Optional[List[SyncTest]] = None,
                 timeout: float = 60.0, threshold: float = 0.6):
        self.a = partner_a
        self.b = partner_b
        self.tests = tests if tests is not None else standard_battery()
        self.timeout = timeout
        self.threshold = threshold

    def _run(self, partner: LLMPartner, test: SyncTest) -> Optional[Fingerprint]:
        try:
            text, ms = partner.timed_complete(test.prompt, system=test.system,
                                              timeout=self.timeout)
        except Exception:
            return None
        return Fingerprint.analyse(text, ms, verify_shell=test.expect_shell)

    def calibrate(self) -> SyncProfile:
        scores: Dict[str, Dict[str, List[Tuple[float, float]]]] = {
            "a": {PartnerRole.CONSCIOUS.value: [], PartnerRole.UNCONSCIOUS.value: []},
            "b": {PartnerRole.CONSCIOUS.value: [], PartnerRole.UNCONSCIOUS.value: []},
        }
        latencies: Dict[str, List[float]] = {"a": [], "b": []}
        prose_hits: Dict[str, List[bool]] = {"a": [], "b": []}
        notes: List[str] = []

        for test in self.tests:
            # Same prompt, same moment, no barrier between them.
            with ThreadPoolExecutor(max_workers=2) as pool:
                fut_a = pool.submit(self._run, self.a, test)
                fut_b = pool.submit(self._run, self.b, test)
                fp_a, fp_b = fut_a.result(), fut_b.result()

            for key, fp, partner in (("a", fp_a, self.a), ("b", fp_b, self.b)):
                if fp is None:
                    # Labelled by partner, not by a role that is not decided yet.
                    notes.append("partner {} ({}) failed test {}".format(
                        key.upper(), partner.metadata.name, test.id))
                    continue
                scores[key][test.role.value].append(
                    (score_response(test, fp), test.weight))
                latencies[key].append(fp.latency_ms)
                if test.role == PartnerRole.CONSCIOUS:
                    # Can this partner produce human language at all?
                    prose_hits[key].append(
                        fp.prose_chars >= 60 and not fp.prose_is_code)

        def weighted(key: str, role: PartnerRole) -> float:
            pairs = scores[key][role.value]
            total_w = sum(w for _, w in pairs)
            if total_w <= 0:
                return 0.0
            return sum(s * w for s, w in pairs) / total_w

        a_con = weighted("a", PartnerRole.CONSCIOUS)
        a_unc = weighted("a", PartnerRole.UNCONSCIOUS)
        b_con = weighted("b", PartnerRole.CONSCIOUS)
        b_unc = weighted("b", PartnerRole.UNCONSCIOUS)

        def prose_capability(key: str) -> float:
            hits = prose_hits[key]
            return (sum(1 for h in hits if h) / len(hits)) if hits else 0.0

        cap_a, cap_b = prose_capability("a"), prose_capability("b")
        mute_a, mute_b = cap_a < 0.34, cap_b < 0.34

        # 2x2 joint assignment: compare complete pairings, not one axis.
        pairing_a_conscious = a_con + b_unc
        pairing_b_conscious = b_con + a_unc

        # A model that cannot produce natural language is barred from the
        # conscious slot at any score — that slot is the only thing that
        # addresses the user. A constraint, not a comparison.
        if mute_a and not mute_b:
            a_is_conscious = False
            notes.append("partner A produced no natural language — code-only, "
                         "barred from the conscious slot")
        elif mute_b and not mute_a:
            a_is_conscious = True
            notes.append("partner B produced no natural language — code-only, "
                         "barred from the conscious slot")
        else:
            if mute_a and mute_b:
                notes.append("NEITHER partner produced natural language — "
                             "nothing here can address the user")
            a_is_conscious = pairing_a_conscious >= pairing_b_conscious
            if abs(pairing_a_conscious - pairing_b_conscious) < 0.05:
                notes.append("pairings are near-identical — assignment is close "
                             "to arbitrary (are both slots the same model?)")

        role_a = PartnerRole.CONSCIOUS if a_is_conscious else PartnerRole.UNCONSCIOUS
        role_b = PartnerRole.UNCONSCIOUS if a_is_conscious else PartnerRole.CONSCIOUS

        # Confidence comes from whoever actually holds the role.
        conf_con = a_con if a_is_conscious else b_con
        conf_unc = b_unc if a_is_conscious else a_unc

        if not scores["a"][PartnerRole.UNCONSCIOUS.value] or \
           not scores["b"][PartnerRole.UNCONSCIOUS.value]:
            notes.append("a partner produced no scorable shell output")

        def mean(xs: List[float]) -> float:
            return sum(xs) / len(xs) if xs else 0.0

        return SyncProfile(
            partner_a=self.a.metadata.to_dict(),
            partner_b=self.b.metadata.to_dict(),
            role_a=role_a.value,
            role_b=role_b.value,
            confidence_conscious=conf_con,
            confidence_unconscious=conf_unc,
            latency_a_ms=mean(latencies["a"]),
            latency_b_ms=mean(latencies["b"]),
            in_sync=(conf_con > self.threshold and conf_unc > self.threshold
                     and not (mute_a and mute_b)),
            test_count=len(self.tests),
            prose_capability_a=cap_a,
            prose_capability_b=cap_b,
            notes=notes,
        )


def main() -> None:
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "calibrate"

    if cmd == "localise" or cmd == "localize":
        import sysmind_strings as strings
        from sysmind_common import save_config
        config = load_config()
        language = config.get("language", "auto")
        if (language or "auto").strip().lower() in ("auto", "english"):
            print("Language is '{}' - interface stays English.".format(language))
            return
        conscious, _ = partners_from_config(config)
        print("Rendering the interface into {} via {}...".format(
            language, conscious.metadata.name))
        rendered = strings.localise(conscious, language)
        strings.save(config, language, rendered)
        save_config(config)
        translated = sum(1 for k, v in rendered.items() if v != strings.BASE[k])
        print("{}/{} strings translated.".format(translated, len(strings.BASE)))
        for k in ("ask_question", "opt_once", "opt_no", "opt_always", "opt_never"):
            print("  {:14} {}".format(k, rendered[k]))
        return

    if cmd == "status":
        try:
            print(SyncProfile.load().summary)
        except FileNotFoundError:
            print("No sync profile yet. Run: sysmind-sync calibrate")
        return

    config = load_config()
    language = config.get("language", "auto")
    a, b = partners_from_config(config)
    tests = standard_battery(language)

    print("Calibrating {} tests against two partners...".format(len(tests)))
    print("  A: {}".format(a.metadata.name))
    print("  B: {}".format(b.metadata.name))
    if language and language.lower() not in ("auto", "english"):
        print("  language probe: {}".format(language))
    print()

    profile = SyncHandshake(a, b, tests=tests).calibrate()
    profile.save()
    print(profile.summary)
    print("\nSaved to {}".format(PROFILE_FILE))


if __name__ == "__main__":
    main()
