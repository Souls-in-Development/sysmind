# Sysmind

A local-AI companion for managing your Linux (Parrot OS) machine — updates, disk space,
security, services — in your own language. Ask in English, Urdu, Arabic, whatever you
think in, and it replies in kind. Commands stay valid shell, always.

> **Platforms.** Linux is the supported platform and the only one with evidence behind it.
> **Windows support is written but has never been run** — PowerShell command generation, a
> Windows-specific block list, `AppData` paths and a PowerShell syntax checker all ship, and
> the code marks itself `tested=False`. If you have a Windows machine, trying it and
> reporting back is genuinely useful. Treat it as an invitation, not a supported platform.
> macOS is not supported: it will start, but the system probes are Linux commands and return
> nothing.

## Two minds, not one

Sysmind runs **two models in two slots**, because no small model is good at both speaking
Urdu and writing correct shell:

| Slot | Job | Speaks |
|------|-----|--------|
| **conscious** | reasoning, explanation, talking to you | your language |
| **unconscious** | shell, configs, system commands | code |

They hand off to each other. The conscious slot turns your request into a task for the
unconscious slot, which writes the command; the conscious slot then explains that command
back to you in your language.

The unconscious slot can be a **pure code model with no natural language at all** — a base
coder that only completes code. It is prompted with comment headers rather than
instructions, so instruction-tuned and base models both work.

**The command you are shown is the command the coder wrote.** The conscious model writes
prose around it and never gets to write the command itself — that is enforced in code, not
asked for in a prompt. It cannot narrate one thing and run another.

Each slot is independently **local or API**:

```json
"slots": {
  "conscious":   {"backend": "ollama", "model": "qwen3:8b"},
  "unconscious": {"backend": "openai", "model": "qwen2.5-coder",
                  "base_url": "https://your-endpoint/v1",
                  "api_key_env": "YOUR_KEY_VAR"}
}
```

Keys are read from the named environment variable — never stored in the config file.
With both slots local, nothing leaves your machine.

## Install

```bash
pip install git+https://github.com/Souls-in-Development/sysmind
sysmind-setup
```

Or from a source copy:

```bash
cd sysmind
./setup.sh
```

Runs a health check, then asks a few questions: how hands-off, how much RAM (picks the
model pair), reply language, scan depth, notifications, launch method.

Then calibrate the pair and go:

```bash
sysmind-sync calibrate
sysmind
```

## Calibration

`sysmind-sync calibrate` runs both models through the same probes at the same moment and
works out which one belongs in which slot. **You don't declare which is which — it
measures.** Put them in backwards and it swaps them.

It checks that the conscious slot can actually produce prose *in your language* (by script,
not by English keywords), and that the unconscious slot produces shell that actually parses.
A model that produces no natural language is barred from the conscious slot outright.

```bash
sysmind-sync calibrate   # run the handshake
sysmind-sync status      # show the current profile
```

## The four modes

| Mode | What happens |
|------|--------------|
| **Advisor** | AI explains, you copy/paste commands yourself |
| **Assistant** | AI suggests, you approve each command |
| **Autopilot** | Anything you've permitted runs; everything else asks |
| **Full Auto** | Dashboard only; opens the menu on demand |

## How permission works

Anything sysmind has not been told about **asks**. Nothing runs unbidden, ever. When it
asks, you have four answers:

```
  [1] run it this time
  [2] do not run it
  [3] always permit this
  [4] never permit this
```

| | this time | remembered |
|---|---|---|
| **1** | runs | no — asks again |
| **2** | doesn't run | no — asks again |
| **3** | runs | **allow list** |
| **4** | doesn't run | **block list** |

**3** and **4** are how you make something known; **1** and **2** are one-off answers.

Answers are digits on purpose. The labels are translated into your language, but no model
reads what you typed — the digit → action mapping is in code, so a bad translation can
confuse you but cannot run the wrong thing.

**Allow list** — narrow and exact. Permitting `apt` does *not* unlock `apt remove`; each
thing earns its own permission. When you say `always`, you choose the breadth yourself:
this exact command, or the whole `apt:install` family.

**Block list** — broad. Refusing `rm` catches `rm -rf /`, `/bin/rm`, `sudo rm`, and
`$(rm ...)`. Refusing a specific command also catches its variants with extra flags. It
ships seeded with `rm`, `dd`, `mkfs` and friends; `blocklist_enabled: false` drops those
defaults but **never** drops entries you declared yourself.

A `never` outranks an `always`. Both are editable in `~/.config/sysmind/config.json`.

Sysmind also remembers how often you approved or rejected each thing, and shows you before
asking again:

```
Run: apt install htop
  history: this exact command: 0x approved, 3x rejected  |  'apt:install': 0x approved, 3x rejected
```

If you keep approving the same family one-off, it offers to permit it properly. That offer
counts **your approvals only** — never the machine's own repetitions — and never appears on
the automatic path, which stays silent.

## Safety

- Every suggested command is parsed before you are offered it — `bash -n` on Linux,
  PowerShell's own parser on Windows. Both read the command without executing it. Anything
  that does not parse is discarded, never shown as runnable.
- **The check fails closed.** If no parser is available at all, nothing is offered rather
  than everything being waved through unverified.
- A fenced block is **one** command, however many lines it spans. A `for` loop runs as a
  loop, not as three broken fragments.
- Only the fence languages that are runnable on your platform count — `bash`/`sh`/`zsh` on
  Linux, `powershell`/`ps1`/`cmd` on Windows. A bare fence is never runnable.
- Commands containing placeholders (`/path/to/…`, `<name>`, `YOUR_KEY`) are rejected. They
  parse fine and look like commands, so nothing else catches them.

## Commands

```bash
sysmind                  # the main menu
sysmind-sync calibrate   # calibrate the model pair
sysmind-sync status      # show the sync profile
sysmind-doctor           # health check
sysmind-scan low         # refresh the system snapshot
```

## Languages, and the models that provide them

**Language support comes from the model in the conscious slot, not from anything
hardcoded here.** Nothing is special-cased per language. Name your language at install
time — any language, typed in full — and the conscious model replies in it.

The installer offers a curated list for that slot:

| Family | Languages | Licence |
|--------|-----------|---------|
| **Qwen3** *(default)* | **119** — including Urdu, Arabic, Hindi, Bengali, Swahili | Apache-2.0 |
| Gemma 3 | 55 major languages, strong translation quality | Gemma terms |
| Command-R | 10 — EN, FR, ES, IT, DE, PT, JA, KO, AR, ZH | CC-BY-NC |
| Aya Expanse | 23 — strong Arabic, Persian, Hindi, Turkish | CC-BY-NC |

Qwen3 is the default because its coverage is the widest; pick another only if you want its
particular strength. If you name a language your chosen family doesn't cover, the installer
says so and falls back to Qwen3 rather than silently giving you a model that can't speak to
you.

The **unconscious slot never changes with language** — shell is shell.

### Measured tiers

These were run against real models, not estimated. Method and evidence below.

| RAM | conscious | unconscious | total | status |
|-----|-----------|-------------|-------|--------|
| 4 GB | `qwen3:1.7b` | `qwen2.5-coder:1.5b` | ~2.4 GB | ❌ **unsuitable — measured** |
| **8 GB** | **`qwen3:8b`** | **`qwen2.5-coder:1.5b`** | **~6 GB** | ✅ **measured good** |
| 16 GB | `qwen3:14b` | `qwen2.5-coder:1.5b` | ~10 GB | untested, expected better |
| 24 GB+ | `qwen3:32b` | `qwen2.5-coder:7b` | ~25 GB | untested |

Every tier now fits its own budget. The previous table did not: the 8 GB row asked for
~10 GB and the top row for ~39 GB.

The coder grows only at the top tier, because a stronger conscious slot writes richer
briefs — when `qwen3:8b` asked for *"list sizes of /var, /usr, /opt, /tmp and /home"*,
the 1.5b coder returned nothing usable. That coupling is observed; the larger coder
sizes are not measured.

**Spend RAM on the conscious slot, not the coder.** That is the main finding, and it is
the opposite of what this table used to say.

### Why the coder is small

The coder's job is narrow: one shell line from a written brief. Measured on the real
calibration battery:

| coder | score | verdict |
|-------|-------|---------|
| `qwen2.5-coder:0.5b` | 0.53 | below the floor — emitted a multi-line script that failed `bash -n` |
| `qwen2.5-coder:1.5b` | 0.80 | **sufficient** |
| `qwen2.5-coder:3b` | 0.80 | no measured gain over 1.5b |

What makes a small coder dangerous is **not its size, it is a vague brief.** The same
1.5b model, given different briefs:

```
"print the ten largest directories under /var"  ->  find /var -maxdepth 1 -type d | sort ...
"clean the disk to free space"                  ->  sudo rm -rf /tmp/* /var/tmp/*
```

So the conscious slot's brief is a **safety control**, not a translation step. It is
required to ask for a command that only inspects when a request is ambiguous.

### Why the conscious slot is large

It carries three jobs: writing that brief, explaining in your language, and asking
permission. Measured:

| | `qwen3:1.7b` | `qwen3:8b` |
|---|---|---|
| briefs in plain language | 0/4 | 4/4 |
| answered the question asked | — | 5/5 |
| produced a safe command | 4/4 | 5/5 |
| Urdu explanation | same sentence repeated ×3 | 8 sentences, 8 distinct |
| permission question named the deletion | **no** | **yes** |

That last row is why 4 GB is marked unsuitable. At 1.7b the question never said the
command would delete anything — a fluent-looking request to approve something the user
had not been told about. This matches the literature: multilingual models on Urdu
produce *"inconsistent or extremely hallucinated responses"* at small sizes.

**Honest limit:** the 8b Urdu was verified to contain the right path and the deletion
verb, and to be free of the repetition seen at 1.7b. Whether it *reads well* to a native
speaker has not been checked.

Expect roughly **100 s per turn** at 8b on CPU — three conscious calls plus one coder
call. Fine for an occasional question, slow as a daily driver.

### How this was measured

Ollama 0.32.1 on Apple Silicon, plus a Debian 12 container for the Linux paths. The
calibration battery in `sysmind_platform` is the same one `sysmind-sync calibrate` runs,
so these numbers are reproducible with:

```bash
sysmind-sync calibrate
```

Two findings changed the design rather than just the table:

**The block list is load-bearing, not a backstop.** Asked *"my disk is full"* in Urdu, the
4 GB pair produced `df -h | grep "^[^ ]" | awk '{print $5}' | xargs rm -rf` — disk
percentages piped into `rm -rf`. The seed list caught it and refused without prompting.
That is the case every guard here exists for, and it only appeared by running the thing.

**Vague briefs cause destructive commands.** `_COMPOSE` now requires the conscious slot to
ask for a command that only inspects when a request is ambiguous, and forbids the verbs
(*clean, free up, fix, tidy, sort out*) that induced deletion. After that change, no
block-list hit occurred in any run — including for requests phrased *"clean it up"* and
*"sort out my disk"*.

A caution for anyone editing these prompts: **concrete examples get parroted.** With
*"disk is full means list the largest directories"* in `_COMPOSE`, both model sizes
answered *"some of my services are broken"* with a disk task. Removing every example fixed
it.

For a language no listed family covers well, any GGUF can be imported with
`ollama create` and named in `config.json`. **Aya Expanse** and **Command-R** are
CC-BY-NC — non-commercial use only.

### Who asks you, and in what language

The unconscious slot cannot address you — it only writes code. So when it produces a
command, the **conscious slot asks you about it, in your language**, naming what the
command will actually do:

```
find /var/log -name '*.gz' -delete
  history: never asked about this before

کیا میں /var/log سے پرانی لاگ فائلیں مستقل طور پر حذف کر دوں؟
  [1] ایک بار چلائیں       (run it this time)
  [2] نہ چلائیں            (do not run it)
  [3] ہمیشہ اجازت دیں      (always permit this)
  [4] کبھی اجازت نہ دیں    (never permit this)
```

The question is written fresh for that specific command, so it says *permanently delete
old log files from /var/log* rather than a generic "run this?". Option labels are rendered
once by the conscious model and cached — run `sysmind-sync localise` after install — with
the English kept alongside as an anchor.

Three rules keep this safe:

- **Answers are digits.** No model reads what you typed; the digit → action mapping is in
  code. A bad translation can confuse you; it cannot run the wrong thing.
- **The literal command is always shown**, above the question, exactly as it will run.
- **If the conscious slot is unreachable**, the cached generic prompt is used. You are
  never left unable to be asked.

The **menu is localised the same way**, and its labels are phrased as inspections —
*"show what is using the most disk space"*, never *"free up disk space"*. That matters
because a menu label is sent to the model verbatim as the request, so vague wording there
would produce exactly the destructive commands `_COMPOSE` exists to prevent.

Still English: the installer wizard's own text, which cannot be localised by a model
because it runs before any model exists. It ships translated into 14 languages instead.

## Status

The logic is covered by tests. **It has not yet been run against real models on a real
Parrot box** — treat the first run as a shakedown.

Tests write to a throwaway directory and never touch your real config:

```bash
python3 -m pytest tests/
```

## Licence

AGPLv3 — see [LICENSE](LICENSE).
