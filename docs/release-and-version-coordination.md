# Release & version coordination — design

Status: proposed · 2026-11-02 · scope: ecosystem (discovery-engine as keystone;
consumers viva-superpowers, vivarium-workbench, and their users)

## The problem

Consumers keep running **out-of-date dependencies** and drift silently out of sync.
A live example from 2026-11-02: the vivarium-workbench dashboard was serving an
investigation slowly and rendering it empty. Root cause — the workbench's `uv.lock`
pinned `discovery-engine` at an old commit, missing a load-time perf fix the engine
had already shipped (`#36`, the mtime-keyed YAML parse cache). Serving the current
engine instead dropped the slow endpoint from ~1.9s to ~0.18s. Nothing had told the
workbench it was behind.

## Diagnosis — discovery-engine is the unreleased keystone

A survey of the core packages:

| package | version | tags | publishes to PyPI |
|---|---|---|---|
| process-bigraph | 1.11.0 | 104 | yes |
| bigraph-schema | 1.4.3 | 126 | yes |
| vivarium-workbench | 0.4.1 | 117 | yes |
| viva-superpowers | 0.22.0 | 32 | yes |
| viva-emitters | 0.2.1 | 10 | yes |
| **discovery-engine** | **0.1.0** | **0** | **no** |

Every core package is released to PyPI and tagged — **except discovery-engine**,
which is `0.1.0`, never tagged, and installed only as a git `branch=main` dependency.
Because the engine can't be pinned by version, every consumer is *forced* onto
`branch=main` for it (workbench and viva-superpowers both source it as `branch=main`).
That one unreleased package cascades the whole git-main dependency chain: a consumer's
lock pins an engine commit, nothing bumps it, and it silently goes stale — the drift
we've been fighting all week (viva-superpowers sat 31 commits behind; the workbench's
runtime engine was stale enough to be slow).

The engine's own `release.yml` even states the intent ("turns `branch=main`
consumption into tagged releases… the foundation of keeping the repos decoupled but
reproducibly in sync") — but it only cuts a GitHub Release and **never publishes to
PyPI**, and was never tagged. The keystone was designed and left unfinished.

## The fix

### 1. Release discovery-engine to PyPI (this PR)

Wire the proven trusted-publisher pattern (bigraph-schema / process-bigraph already
use it) into the engine's `release.yml`: on a `v*` tag, verify tag==pyproject version,
`uv build`, publish to PyPI via OIDC trusted publishing, then cut the GitHub Release.

**One-time setup (owner: Eran / org admin):** register the `discovery-engine` project
on PyPI and add this repo + the `release` workflow + the `release` environment as a
trusted publisher (no stored token). Then push `v0.1.0` to cut the first release.

### 2. Flip consumers from git-main to semver (staged, per consumer)

Once the engine publishes versions, each consumer changes
`discovery-engine = { git = …, branch = "main" }` to a PyPI semver range
(`discovery-engine>=0.1,<0.2` while pre-1.0). The engine's `downstream.yml` already
proves viva-superpowers' facade contract against engine HEAD before a change lands, so
a release is known-good for its primary consumer. viva-superpowers (also released)
then gets the same treatment from *its* consumers, collapsing the git-main chain into
normal semver dependency management.

### 3. This is what serves GitHub users

A user who installs the workbench from GitHub today gets either the lock's **stale**
pinned deps or a lock-less **untested-latest** resolve — and git deps can't be a clean
`pip install`. After steps 1–2, `pip install vivarium-workbench` pulls a **coherent,
released, tested set** (the workbench release plus its pinned, also-released deps). No
git-main roulette; "out of date" becomes a normal, visible dependabot/Renovate bump.

### 4. Keep it from drifting again (already built)

- **Frozen CI + drift canary** (viva-superpowers #393, vivarium-workbench #1553): the
  regular suite installs frozen from the lock (main stays green against what it was
  tested with); a daily canary installs upstreams **live** and runs the suite, so drift
  surfaces same-day with a dated signal. See
  `viva-superpowers:docs/specs/2026-11-02-cross-repo-version-coordination-design.md`.
- **Renovate `lockFileMaintenance`** (viva-superpowers #391) opens the weekly,
  tested lock-bump PR. Once the engine publishes versions, Renovate tracks the **tag**
  bump too. (Open question — owner Eran: is the Renovate app enabled org-wide? If not,
  a scheduled `uv lock --upgrade-package` fallback.)
- **downstream.yml** (this repo): engine changes are proven against the consumer
  contract before they land — the in-sync guard from the producer side.

## Sequence & ownership

1. **This PR** — engine `release.yml` publishes to PyPI. Review: discovery-engine owner.
2. **PyPI trusted-publisher registration** + first `v0.1.0` tag — Eran (org admin).
3. **Consumer flips** to semver (viva-superpowers, then workbench) — per-repo, staged
   after the first engine release; overlaps the workbench production-deployment RFC
   (`vivarium-workbench:docs/REFACTOR-PLAN.md`, Jim + Alex) and should sequence with it.
4. **Generalize** frozen+canary+Renovate to any remaining consumers.

The net: the engine stops being a moving branch, consumers pin versions, GitHub users
install a coherent released set, and the frozen+canary+Renovate seam keeps the pins
current and tested instead of silently stale.
