# Sysmind

A local-AI companion for managing your Linux (Parrot OS) machine — updates, disk space,
security, services — in your own language. Ask in English, Urdu, Arabic, whatever you
think in, and it replies in kind. Commands stay valid shell, always.

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
Execute? [Y/n/always/never]
```

| | this time | remembered |
|---|---|---|
| `Y` | runs | no — asks again |
| `n` | doesn't run | no — asks again |
| `always` | runs | **allow list** |
| `never` | doesn't run | **block list** |

`always` and `never` are how you make something known. `Y` and `n` are one-off answers.

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

- Every suggested command is parsed with `bash -n` before you are offered it. Invalid shell
  is discarded, never shown as runnable.
- A fenced block is **one** command, however many lines it spans. A `for` loop runs as a
  loop, not as three broken fragments.
- Only `bash`/`sh`/`shell`/`zsh`/`console` fences are treated as runnable. A bare fence is not.

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

The **unconscious slot never changes with language** — shell is shell:

| RAM | conscious | unconscious |
|-----|-----------|-------------|
| 4 GB | `qwen3:1.7b` | `qwen2.5-coder:1.5b` |
| **8 GB** | **`qwen3:8b`** | **`qwen2.5-coder:7b`** |
| 16 GB | `qwen3:14b` | `qwen2.5-coder:14b` |
| 24–32 GB | `qwen3:32b` | `qwen3-coder:30b` |

Bigger conscious models speak every language better, so size that slot up if RAM allows.
Two models on 8 GB is tight — Ollama swaps them on demand, or point one slot at an API
endpoint.

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

Still English: the main menu and the installer wizard.

## Status

The logic is covered by tests. **It has not yet been run against real models on a real
Parrot box** — treat the first run as a shakedown.

Tests write to a throwaway directory and never touch your real config:

```bash
python3 -m pytest tests/
```

## Licence

AGPLv3 — see [LICENSE](LICENSE).
