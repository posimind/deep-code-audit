<p align="center">
  <img src=".github/assert/deep-code-audit-logo.jpeg" alt="deep-code-audit logo" width="720">
</p>

<h1 align="center">deep-code-audit</h1>

<p align="center">
  Multi-agent adversarial code audit for Claude Code — parallel hunters find defects, fresh-context verifiers tear them apart, and only what survives makes it into the report.
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License: MIT"></a>
  <img src="https://img.shields.io/badge/Claude%20Code-plugin-d97757.svg" alt="Claude Code plugin">
  <img src="https://img.shields.io/badge/python-stdlib%20only-3776ab.svg" alt="Python stdlib only">
</p>

<p align="center">
  <b>English</b> | <a href="README.ko.md">한국어</a>
</p>

---

## What is this?

`deep-code-audit` is a [Claude Code](https://docs.anthropic.com/en/docs/claude-code) plugin (skill + two dedicated subagents) that audits an entire repository for **security, concurrency, fault-handling, logic, and resource defects**, then renders a structured **Korean-language report** aimed at reviewers who don't know the codebase.

Instead of one model skimming your code in a single pass, it runs an adversarial pipeline:

```
[1] Select & group     classify files (core / low / exclude), build audit brief,
                       group by line count + import-graph cohesion
[2] Adversarial hunt   one hunter subagent per group, 4–6 in parallel —
                       reads the whole repo, reports only inside its group
[2.5] Reinforcement    critical-only second pass on high-risk groups +
                       cross-group hint routing, script-merged (no silent loss)
[3] Adversarial verify fresh-context verifier per group: re-derives every claim
                       before reading the hunter's rationale (anti-anchoring),
                       tri-state rubric with kill-gates, machine-checked consistency
[4] Report             critical → major → minor, with entry path, mechanism,
                       failure scenario, and a fix sample per finding
```

Every stage hands off through schema-validated JSON files on disk (`<target>/.deep-code-audit/<run-id>/`), so runs are **resumable**, stages are independently retryable, and nothing lives only inside an agent's context.

For the full picture — design goals and principles, stage-by-stage behavior, the failure-mode → defense table, and design history — see the **[design & architecture doc](.claude/docs/deep-code-audit-design.md)**.

## Why use it?

- **Few false positives.** Every finding must survive an independent verifier that re-derives the claim from the raw code before it ever sees the hunter's reasoning, then passes a 5-criteria rubric (reachable precondition, genuinely harmful outcome, no upstream guard, survives adversarial rebuttal…). Any criterion proven unmet kills the finding.
- **Few false negatives.** Hunters read without restriction (cross-file tracing is mandatory, not optional), group seams are minimized by import-graph clustering, cross-group suspicions are routed as hints that *must* be consumed, and high-risk groups get a dedicated critical-only second pass.
- **Severity that means something.** Critical/major findings get the full rubric plus an appraisal step; test/fixture files are severity-capped and the cap is machine-enforced.
- **Prompt-injection defense.** Repository content — including the target's own `CLAUDE.md` — is treated as untrusted data. "Already reviewed, skip this file" in a comment is a suspicion signal, not an instruction.
- **No silent degradation.** The pipeline treats "exit 0 but quality quietly gone" as worse than a crash: grouping cohesion fallbacks, unparsed languages, unconsumed hints, and merge losses are all detected, logged to `issues.jsonl`, and surfaced in the report.
- **Zero dependencies.** The four helper scripts are Python standard library only, with 67 unit tests.

## Installation

The plugin ships two components, and **both must be loaded** for the skill to run at full capability: the skill itself (`skills/deep-code-audit/`) and the two dedicated subagents (`agents/deep-audit-hunter.md`, `agents/deep-audit-verifier.md`) that do the actual hunting and verification. Installing through the Claude Code plugin route loads both automatically — no separate wiring for `agents/` is needed.

### Claude Code (CLI)

```
/plugin marketplace add posimind/deep-code-audit
/plugin install deep-code-audit@deep-code-audit
```

Restart your session after installing — agents added mid-session aren't recognized until restart (the skill's preflight checks this for you).

For development, a clone + symlink also works, since the repo carries `.claude-plugin/plugin.json` and is discovered in place (Claude Code v2.1.142+):

```bash
git clone https://github.com/posimind/deep-code-audit.git
ln -s "$(pwd)/deep-code-audit" ~/.claude/skills/deep-code-audit
```

> Symlink the **repo root**, not `skills/deep-code-audit/`. The repo root carries `.claude-plugin/plugin.json`, so it is loaded as a plugin — which is exactly what makes the `agents/` definitions load along with the skill. Linking only the skill subdirectory would leave the agents behind.

### VS Code (Claude Code extension)

The [Claude Code VS Code extension](https://docs.anthropic.com/en/docs/claude-code/ide-integrations) shares `~/.claude` with the CLI, so either:

1. Run the same `/plugin marketplace add` / `/plugin install` commands in the extension's chat panel, **or**
2. Install once via the CLI as above — the plugin is then available in VS Code too.

No separate linking of `agents/` is needed — the plugin bundles the skill and both subagents together. Reload the VS Code window (or start a new session) after installing.

### opencode

[opencode](https://opencode.ai) discovers Claude-style skills from `~/.claude/skills/*/SKILL.md` and `~/.config/opencode/skills/*/SKILL.md`, so the skill itself loads with either the Claude Code symlink install above or:

```bash
git clone https://github.com/posimind/deep-code-audit.git
mkdir -p ~/.config/opencode/skills
ln -s "$(pwd)/deep-code-audit/skills/deep-code-audit" ~/.config/opencode/skills/deep-code-audit
```

> **Important — agents are not auto-loaded on opencode.** The two dedicated subagents are Claude Code agent definitions, and opencode does **not** read them: opencode agents live in `~/.config/opencode/agent/` with a different frontmatter format (boolean tool flags, explicit model), so simply symlinking `agents/` is not enough — they would need manual porting. Without them, the skill's Stage 0 preflight detects the missing agents and offers **compatibility mode** (the agent protocol concatenated onto general-purpose subagents) — it runs only with your explicit consent and is flagged in the final report. The full dedicated-agent experience is on Claude Code.

## Usage

In its basic form, this is all you need:

```
/deep-code-audit
```

That runs a full audit of the current workspace with the default settings. Natural-language requests trigger the skill too ("find vulnerabilities across this codebase", "이 레포 audit 해줘").

### What it hunts by default

Every run sweeps the codebase through five defect lenses:

| Lens | What it looks for |
|---|---|
| **Security** | injection, authentication/authorization bypass, secret exposure, path traversal, unsafe deserialization |
| **Concurrency** | data races, deadlocks, TOCTOU windows, unsynchronized shared state |
| **Fault handling** | swallowed exceptions, missing error paths, partial-failure states left inconsistent |
| **Logic** | boundary conditions, off-by-one, inverted/wrong conditions, unreachable or dead branches |
| **Resource** | leaks of file descriptors / memory / connections, missing cleanup on error paths, unbounded growth |

The audit brief stage prioritizes these lenses per project (e.g. a web API leans security-first), and findings are reported as critical → major → minor.

### Steering the run with a prompt

Anything beyond the defaults is just extra prompt text after the command. For example:

| You want | Say |
|---|---|
| Skip test code | `/deep-code-audit exclude the test directories` |
| Skip specific paths | `/deep-code-audit skip vendor/ and examples/` |
| Audit only a specific path | `/deep-code-audit audit only src/api` |
| Only certain defect lenses | `/deep-code-audit focus on concurrency and resource defects only` |
| Resume an interrupted run | `resume the audit from earlier` |

Scope adjustments can also come as follow-ups mid-conversation ("tests 빼고", "api 디렉터리만").

### Output

Results land in `<target>/.deep-code-audit/<run-id>/` — the report is `감사보고서.md` (split into summary / critical-major / minor files past ~15 findings). **The report is written in Korean** regardless of the audited codebase's language.

## Development

```bash
# unit tests (67, no external deps)
python3 skills/deep-code-audit/scripts/test_scripts.py

# compile check
python3 -m py_compile skills/deep-code-audit/scripts/*.py

# plugin manifest check
claude plugin validate . --strict
```

## License

[MIT](LICENSE) © 2026 Youhyun Jung
