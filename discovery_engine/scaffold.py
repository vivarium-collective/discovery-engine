"""Scaffold a workspace by copying viva-template's payload and rendering it.

viva-template nests its scaffold payload under a `template/` subdir; the repo
root holds only viva-template's own dev infra and the GitHub "Use this template"
entry point. The plugin scaffolder copies `template/`'s contents directly.

Two modes:
- Local source (path) — copies `<source>/template/`
- Remote source (git URL) — git clone --depth 1, then copies `template/`

After source acquisition, runs template-init.sh non-interactively with the
workspace name piped on stdin.
"""
from __future__ import annotations
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import click


# Requires viva-template at the payload-boundary restructure (commit on or
# after 2026-05-12) or later — earlier versions lack the template/ subdir
# and will fail because that subdir is absent.
DEFAULT_REMOTE = "https://github.com/vivarium-collective/viva-template.git"


def _looks_like_path(s: str) -> bool:
    """Heuristic: source is a local path if it starts with /, ~, ., or is an existing dir."""
    if s.startswith(("/", "~", ".")):
        return True
    p = Path(s)
    return p.exists() and p.is_dir()


def _resolve_source(source: str | None) -> str:
    if source:
        return source
    env = os.environ.get("VIVA_TEMPLATE") or os.environ.get("PBG_TEMPLATE")
    if env:
        return env
    return DEFAULT_REMOTE


def _acquire(source: str, target: Path) -> None:
    """Land the contents of viva-template's `template/` subdir into `target`."""
    if _looks_like_path(source):
        src = Path(os.path.expanduser(source)).resolve()
        if not src.is_dir():
            raise click.ClickException(f"local template source not a directory: {src}")
        payload = src / "template"
        if not payload.is_dir():
            raise click.ClickException(f"template/ subdir missing in source: {payload}")
        # Allow target to be a pre-existing empty dir (scaffold_workspace already
        # rejected non-empty); dirs_exist_ok=True lets copytree overlay into it.
        shutil.copytree(payload, target, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns(".git"))
    else:
        with tempfile.TemporaryDirectory() as tmp:
            clone_dir = Path(tmp) / "viva-template"
            try:
                subprocess.run(
                    ["git", "clone", "--depth", "1", source, str(clone_dir)],
                    check=True,
                )
            except subprocess.CalledProcessError:
                raise click.ClickException(f"git clone failed for source: {source}")
            payload = clone_dir / "template"
            if not payload.is_dir():
                raise click.ClickException(
                    f"template/ subdir missing in cloned source: {source}"
                )
            # Copy only the payload — the clone's .git stays in the temp dir,
            # so the new workspace gets a fresh history.
            shutil.copytree(payload, target, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns(".git"))


def _render(target: Path, workspace_name: str) -> None:
    init_script = target / "template-init.sh"
    if not init_script.exists():
        raise click.ClickException(f"template-init.sh missing in source: {init_script}")
    try:
        subprocess.run(
            ["bash", str(init_script)],
            input=f"{workspace_name}\n",
            text=True, cwd=target, check=True,
        )
    except subprocess.CalledProcessError as e:
        raise click.ClickException(
            f"template-init.sh exited {e.returncode} (workspace name: {workspace_name})"
        ) from e


def scaffold_workspace(target: Path, workspace_name: str, source: str | None = None) -> Path:
    """Public entry point. Returns the target path on success."""
    if target.exists() and any(target.iterdir()):
        raise click.ClickException(f"{target} exists and is non-empty")
    target.parent.mkdir(parents=True, exist_ok=True)
    src = _resolve_source(source)
    _acquire(src, target)
    _render(target, workspace_name)
    return target


# ---------------------------------------------------------------------------
# In-place workspace promotion (Slice A of the mem3dg-readdy onboarding fix)
# ---------------------------------------------------------------------------

# Files in viva-template's `template/` tree that MUST NOT be copied into an
# existing repo — they would clobber the repo's own README, pyproject, etc.
# Encoded here rather than in prose so the conflict set is a single source of
# truth; the friction log §5 lists these with rationales.
_INPLACE_SKIP_FILES = frozenset({
    "README.md.j2",       # composite repos have richer model READMEs
    "pyproject.toml.j2",  # MERGE separately — see _inplace_merge_pyproject
    ".gitignore",         # MERGE separately — see _inplace_merge_gitignore
    "template-init.sh",   # this function IS that script's job
})

# Substitution vars matching template-init.sh. Single source of truth for
# what placeholders the renderer recognizes; bumped from one site only.
_INPLACE_PLACEHOLDERS = ("workspace_name", "package_path", "today",
                         "plugin_version", "generated_at")


def _plugin_version() -> str:
    """Read viva-superpowers' own __version__ — replaces template-init.sh's
    hardcoded constant. Falls back to a literal if the package didn't expose
    one (shouldn't happen but defensive)."""
    try:
        from . import __version__  # type: ignore[attr-defined]
        return str(__version__)
    except Exception:
        return "0.0.0"


def _inplace_render_subs(workspace_name: str, package_path: str) -> dict:
    """Build the substitution dict for .j2 rendering."""
    import datetime as _dt
    today = _dt.date.today().isoformat()
    plugin_version = _plugin_version()
    return {
        "workspace_name":  workspace_name,
        "package_path":    package_path,
        "today":           today,
        "generated_at":    today,
        "plugin_version":  plugin_version,
    }


def _inplace_extract_template_deps(template_pyproject_text: str) -> list[str]:
    """Pull the `dependencies = [...]` array out of the template's pyproject.

    Returns the list of dep strings (e.g. ['process-bigraph', 'pyyaml>=6.0',
    'vivarium-workbench']). Used to compute the diff against an existing
    pyproject's deps when merging.
    """
    import tomllib
    parsed = tomllib.loads(template_pyproject_text)
    project = parsed.get("project") or {}
    deps = project.get("dependencies") or []
    return list(deps)


def _template_requires_python(template_pyproject_text: str) -> str | None:
    """Pull `requires-python` out of the template's pyproject, or None."""
    import tomllib
    parsed = tomllib.loads(template_pyproject_text)
    return (parsed.get("project") or {}).get("requires-python")


def _requires_python_floor(spec: str | None) -> tuple[int, ...] | None:
    """Parse the lower bound out of a requires-python specifier.

    Returns a version tuple like ``(3, 11)`` for the highest ``>=``/``~=``/``==``
    bound found (e.g. ``">=3.10,<4"`` -> ``(3, 10)``), or None when there is no
    lower bound to compare.
    """
    if not spec:
        return None
    import re as _re
    best: tuple[int, ...] | None = None
    for m in _re.finditer(r"(?:>=|~=|==)\s*([0-9]+(?:\.[0-9]+)*)", spec):
        parts = tuple(int(p) for p in m.group(1).split("."))
        if best is None or parts > best:
            best = parts
    return best


def _inplace_merge_requires_python(
    existing_path: Path, template_requires_python: str | None,
) -> str | None:
    """Raise the host pyproject's `requires-python` to the template's floor
    when the template's is higher (or set it when the host declares none).

    Returns the new `requires-python` string if it was changed, else None.

    The in-place scaffolder merges the template's dependencies into the host
    pyproject (e.g. bigraph-viz>=2.0.3, which needs py>=3.11). If the host still
    declares a lower floor like `>=3.10`, `uv lock`/`uv sync` can't resolve the
    merged deps. (#357)
    """
    tpl_floor = _requires_python_floor(template_requires_python)
    if tpl_floor is None:
        return None
    text = existing_path.read_text(encoding="utf-8")
    import tomllib
    host_rp = (tomllib.loads(text).get("project") or {}).get("requires-python")
    host_floor = _requires_python_floor(host_rp)
    if host_floor is not None and host_floor >= tpl_floor:
        return None

    import re as _re
    if host_rp is not None:
        new_text, n = _re.subn(
            r'(^\s*requires-python\s*=\s*)(["\']).*?\2',
            lambda m: f'{m.group(1)}"{template_requires_python}"',
            text, count=1, flags=_re.MULTILINE,
        )
        if n == 0:
            click.echo(
                "warning: could not rewrite `requires-python` in "
                f"{existing_path}; set it to {template_requires_python!r} "
                "manually (the merged template deps need it)", err=True,
            )
            return None
    else:
        # Host declares no requires-python at all — insert one under [project].
        new_text, n = _re.subn(
            r"(^\s*\[project\]\s*\n)",
            rf'\1requires-python = "{template_requires_python}"\n',
            text, count=1, flags=_re.MULTILINE,
        )
        if n == 0:
            click.echo(
                "warning: no [project] table in "
                f"{existing_path}; add requires-python = "
                f"{template_requires_python!r} manually", err=True,
            )
            return None

    existing_path.write_text(new_text)
    click.echo(
        f"raised requires-python to {template_requires_python!r} to match the "
        "template (its merged dependencies require it)"
    )
    return template_requires_python


def _inplace_merge_pyproject(existing_path: Path, template_deps: list[str]) -> list[str]:
    """Append any template deps that aren't already in the existing
    pyproject's [project].dependencies array. Returns the list of deps
    actually added (for surfacing in the success message).

    Conservative: requires the existing pyproject to use the multi-line
    array form `dependencies = [\\n    "...",\\n    ...\\n]`. If the array
    is single-line or formatted unexpectedly, prints a clear "add these
    manually" hint instead of risking a corrupting edit.
    """
    import tomllib
    existing_text = existing_path.read_text(encoding="utf-8")
    try:
        existing_parsed = tomllib.loads(existing_text)
    except tomllib.TOMLDecodeError as e:
        raise click.ClickException(
            f"existing pyproject.toml at {existing_path} is not valid TOML: {e}"
        )
    existing_deps = set(
        (existing_parsed.get("project") or {}).get("dependencies") or []
    )
    # Compare by dep name only — versions can differ without that being drift.
    existing_names = {_dep_name(d) for d in existing_deps}
    to_add = [d for d in template_deps if _dep_name(d) not in existing_names]
    if not to_add:
        return []

    # Find the multi-line dependencies array. Pattern: `dependencies = [\n`
    # ... `]` at the start of a line.
    import re as _re
    pat = _re.compile(r"(^\s*dependencies\s*=\s*\[\n)(.*?)(^\s*\]\s*$)",
                      _re.MULTILINE | _re.DOTALL)
    m = pat.search(existing_text)
    if not m:
        click.echo(
            "warning: existing pyproject.toml doesn't have a multi-line "
            "`dependencies = [\\n  ...\\n]` array. Add these template deps "
            "manually:\n  " + "\n  ".join(f'"{d}"' for d in to_add),
            err=True,
        )
        return []
    insertion = "".join(f'    "{d}",\n' for d in to_add)
    new_text = existing_text[:m.end(2)] + insertion + existing_text[m.end(2):]
    existing_path.write_text(new_text)
    return to_add


def _inplace_merge_pyproject_cli_extra(existing_path: Path) -> bool:
    """Ensure [project.optional-dependencies] declares cli = ["typer", "rich"]
    so the scaffolded cli/ subpackage (see _inplace_create_cli) is
    installable via `uv pip install -e ".[cli]"`. Returns True if added;
    leaves an existing `cli` extra untouched (don't clobber user edits)."""
    import tomllib
    text = existing_path.read_text(encoding="utf-8")
    parsed = tomllib.loads(text)
    extras = (parsed.get("project") or {}).get("optional-dependencies") or {}
    if "cli" in extras:
        return False
    entry = 'cli = ["typer", "rich"]\n'
    if "[project.optional-dependencies]" in text:
        text = re.sub(r"(\[project\.optional-dependencies\]\s*\n)",
                      rf"\1{entry}", text, count=1)
    else:
        sep = "" if text.endswith("\n") else "\n"
        text = f"{text}{sep}\n[project.optional-dependencies]\n{entry}"
    existing_path.write_text(text)
    return True


def _inplace_merge_pyproject_scripts(
    existing_path: Path, workspace_name: str, package_path: str,
) -> bool:
    """Ensure [project.scripts] declares
    <workspace_name> = "<package_path>.cli.__main__:main" so the scaffolded
    cli/ subpackage installs as a real console-script entrypoint. Returns
    True if added; leaves a pre-existing entry for this name untouched
    (don't clobber a user's own script mapping)."""
    import tomllib
    text = existing_path.read_text(encoding="utf-8")
    parsed = tomllib.loads(text)
    scripts = (parsed.get("project") or {}).get("scripts") or {}
    entry_value = f"{package_path}.cli.__main__:main"
    if workspace_name in scripts:
        if scripts[workspace_name] != entry_value:
            click.echo(
                f"warning: [project.scripts] already maps '{workspace_name}' "
                f"to {scripts[workspace_name]!r}; leaving it as-is "
                f"(wanted {entry_value!r})", err=True,
            )
        return False
    entry = f'{workspace_name} = "{entry_value}"\n'
    if "[project.scripts]" in text:
        text = re.sub(r"(\[project\.scripts\]\s*\n)", rf"\1{entry}", text, count=1)
    else:
        sep = "" if text.endswith("\n") else "\n"
        text = f"{text}{sep}\n[project.scripts]\n{entry}"
    existing_path.write_text(text)
    return True


def _dep_name(dep_spec: str) -> str:
    """Extract the package name from a PEP-440 dep spec like 'foo>=1.0' → 'foo'."""
    import re as _re
    return _re.split(r"[<>=!~\s\[]", dep_spec, maxsplit=1)[0].strip().lower()


def _inplace_merge_gitignore(existing_path: Path, template_lines: list[str]) -> int:
    """Append any template .gitignore lines not already present in the
    existing .gitignore. Returns the count of lines added."""
    if existing_path.exists():
        existing = existing_path.read_text(encoding="utf-8").splitlines()
    else:
        existing = []
    existing_set = {ln.strip() for ln in existing if ln.strip()}
    to_add = [ln for ln in template_lines if ln.strip()
              and not ln.strip().startswith("#")
              and ln.strip() not in existing_set]
    if not to_add:
        return 0
    sep = "\n" if (existing and existing[-1].strip()) else ""
    new_text = existing_path.read_text(encoding="utf-8") if existing_path.exists() else ""
    new_text += sep + "\n# Added by viva-superpowers in-place scaffolder\n"
    new_text += "\n".join(to_add) + "\n"
    existing_path.write_text(new_text)
    return len(to_add)


def _inplace_autopin_vivarium(pyproject_path: Path) -> str | None:
    """Mirror of template-init.sh's auto-pin block. If a sibling
    `../vivarium-workbench/` exists and the pyproject doesn't already
    declare `[tool.uv.sources]`, append a vivarium-workbench pin.

    Returns the absolute path that got pinned, or None if no pin was made
    (either because the sibling is absent or the section already exists)."""
    text = pyproject_path.read_text(encoding="utf-8")
    if "[tool.uv.sources]" in text:
        return None  # don't clobber user-set sources
    env_path = os.environ.get("VIVARIUM_WORKBENCH_PATH")
    candidates = []
    if env_path:
        candidates.append(Path(env_path).expanduser().resolve())
    candidates.append((pyproject_path.parent.parent / "vivarium-workbench").resolve())
    sibling = next((c for c in candidates
                    if c.is_dir() and (c / "pyproject.toml").is_file()),
                   None)
    if sibling is None:
        return None
    sep = "" if text.endswith("\n") else "\n"
    pyproject_path.write_text(
        f"{text}{sep}\n[tool.uv.sources]\n"
        f'vivarium-workbench = {{ path = "{sibling}", editable = true }}\n'
    )
    return str(sibling)


def _inplace_create_pkg(workspace_root: Path, package_path: str) -> bool:
    """Create pbg_<slug>/__init__.py + core.py if the package dir doesn't
    already exist. Returns True if files were created."""
    pkg_dir = workspace_root / package_path
    if pkg_dir.is_dir() and (pkg_dir / "__init__.py").is_file():
        return False
    pkg_dir.mkdir(parents=True, exist_ok=True)
    (pkg_dir / "__init__.py").write_text(
        f'"""{package_path} — workspace Python package."""\n'
    )
    (pkg_dir / "core.py").write_text(
        '"""build_core(core=None) — the workspace core.\n\n'
        'Cross-repo convention: compose the cores of imported repos (inheriting\n'
        'their registered processes/types), then register THIS repo\'s own types,\n'
        'processes, and composites — so a downstream importer gets everything by\n'
        'calling this build_core.\n'
        '"""\n'
        'from process_bigraph import allocate_core\n'
        'from discovery_engine.core_compose import (\n'
        '    compose_import_cores,\n'
        '    register_package_processes,\n'
        ')\n\n\n'
        'def build_core(core=None):\n'
        '    core = core if core is not None else allocate_core()\n'
        '    # 1) Compose imported repos\' cores. Standard pbg-* imports\n'
        '    #    auto-discover via allocate_core once installed; list any import\n'
        '    #    whose processes a composite instantiates DIRECTLY so they still\n'
        '    #    register by name, e.g.:\n'
        '    #    compose_import_cores(core, ["viva_munk", "some_dep.core"])\n'
        '    # 2) Register THIS repo\'s own process/step classes as first-class,\n'
        '    #    browsable Registry entries (a composite that instantiates a\n'
        '    #    process directly does NOT auto-register it by name). No-op until\n'
        f'    #    {package_path}/processes/ exists.\n'
        f'    register_package_processes(core, "{package_path}.processes")\n'
        '    #    core.register_types({...})  # this repo\'s own types\n'
        '    return core\n'
    )
    return True


def _inplace_create_cli(workspace_root: Path, workspace_name: str, package_path: str) -> bool:
    """Create <package_path>/cli/{__init__.py,__main__.py} if the cli
    subpackage doesn't already exist. Returns True if files were created.

    Mirrors viva-template's own template-init.sh cli scaffold byte-for-byte
    (module-level content only — package_path/workspace_name are the two
    substitution points), so a workspace scaffolded in-place via this plugin
    and one scaffolded fresh via viva-template end up with the same CLI.
    """
    cli_dir = workspace_root / package_path / "cli"
    if cli_dir.is_dir() and (cli_dir / "__init__.py").is_file():
        return False
    cli_dir.mkdir(parents=True, exist_ok=True)
    (cli_dir / "__init__.py").write_text(
        f'"""{package_path}.cli — the workspace\'s command-line interface."""\n'
    )
    (cli_dir / "__main__.py").write_text(_CLI_MAIN_TEMPLATE.format(
        package_path=package_path, workspace_name=workspace_name,
    ))
    return True


# Kept in parity with viva-template's template/template-init.sh CLI heredoc —
# the common, generic command interface every viva-* workspace/wrapper gets.
# {package_path} / {workspace_name} are the only substitution points.
_CLI_MAIN_TEMPLATE = '''"""{package_path}.cli.__main__ — the workspace's CLI entrypoint.

Common, generic command interface for viva-* workspaces: `run` builds and
runs any catalog composite (spec or generator) directly against
process-bigraph's own discovery + Composite APIs — the same primitives the
dashboard's composite resolver (`vivarium_workbench.lib.composite_resolve`)
is itself built on, minus that resolver's dashboard/cloud-dispatch layers,
which don't apply to a plain local CLI run. No server required. Add
workspace-specific sub-CLIs as sibling modules under cli/ and mount them on
`app` below.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

import typer
import yaml
from rich.console import Console

app = typer.Typer(name="{workspace_name}", help="{workspace_name} workspace CLI.")
console = Console()


@app.callback()
def _callback() -> None:
    """{workspace_name} workspace CLI.

    Keeps `run` an explicit subcommand (`{workspace_name} run ...`) even while
    it is the only command — Typer collapses a single `@app.command` into the
    bare top-level invocation unless a callback is registered. Add
    workspace-specific sub-CLIs as more `@app.command`s below, or mount
    sibling Typer apps here with `app.add_typer(...)`.
    """


def _find_workspace_root(start: "Path | None" = None) -> Path:
    """Walk up from `start` (default cwd) to the nearest workspace.yaml."""
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "workspace.yaml").is_file():
            return candidate
    raise typer.BadParameter(
        "no workspace.yaml found in this directory or any parent"
    )


def _package_path(workspace_root: Path) -> str:
    ws_data = yaml.safe_load(
        (workspace_root / "workspace.yaml").read_text(encoding="utf-8")
    ) or {{}}
    return ws_data.get("package_path") or "{package_path}"


def _parse_override(raw: str) -> tuple[str, Any]:
    """Parse a `key=value` override; value is YAML-loaded so ints/floats/
    bools/lists parse naturally and plain strings still round-trip."""
    if "=" not in raw:
        raise typer.BadParameter(f"override must be key=value, got: {{raw!r}}")
    key, _, value = raw.partition("=")
    return key.strip(), yaml.safe_load(value)


def _resolve_composite_spec(workspace_root: Path, package_path: str, composite_id: str):
    """Resolve `composite_id` to a live process_bigraph CompositeSpec.

    Covers both composite conventions this ecosystem uses — a static
    `*.composite.yaml`/`.json` file under the workspace package, and a
    `@composite_spec`/`@composite_generator`-decorated Python generator —
    since both register into the same process_bigraph.composite_spec
    registry. `discover_specs` alone only reaches installed packages'
    top-level modules (an editable install of THIS workspace's own package
    is invisible to it — same caveat as build_core()'s own discovery, see
    core.py); when the id still misses, import the module the id names
    (a generator id is `<dotted.module>.<generator_name>`) so its decorator
    fires, then retry.
    """
    from process_bigraph.composite_spec import discover_specs, get as get_spec

    if str(workspace_root) not in sys.path:
        sys.path.insert(0, str(workspace_root))

    discover_specs(workspace=workspace_root / package_path)
    spec = get_spec(composite_id)
    if spec is None and "." in composite_id:
        module_name = composite_id.rsplit(".", 1)[0]
        try:
            importlib.import_module(module_name)
        except Exception:
            pass
        spec = get_spec(composite_id)
    if spec is None:
        raise typer.BadParameter(f"composite not found: {{composite_id}}")
    return spec


@app.command(name="run")
def run_composite(
    composite_id: str = typer.Argument(
        ..., help="Dotted composite reference, e.g. pkg.composites.my_model"
    ),
    steps: float = typer.Option(
        None, "--steps",
        help="Simulation duration in steps. Defaults to the composite's own default_n_steps, or 10.",
    ),
    emit: str = typer.Option(
        None, "--emit", help="Comma-separated '/'-joined store paths to print. Defaults to the full state."
    ),
    param: list[str] = typer.Option(
        [], "--param", help="Parameter override as key=value (repeatable)."
    ),
) -> None:
    """Build and run a catalog composite via process-bigraph directly.

    The generalized, built-in execution entrypoint: resolve `composite_id`
    to a CompositeSpec, then build+run it with the spec's own
    `to_composite()` (process-bigraph's single canonical builder for both
    spec-file and generator-decorated composites — it normalizes either
    return shape and installs the spec's declared emitters). No dashboard
    server involved.
    """
    workspace_root = _find_workspace_root()
    package_path = _package_path(workspace_root)
    core_module = importlib.import_module(f"{{package_path}}.core")
    core = core_module.build_core()

    overrides = dict(_parse_override(p) for p in param)
    spec = _resolve_composite_spec(workspace_root, package_path, composite_id)
    composite = spec.to_composite(overrides, core=core)
    duration = steps if steps is not None else (spec.default_n_steps or 10)
    composite.run(duration)

    if emit:
        for path in (p.strip() for p in emit.split(",") if p.strip()):
            value: Any = composite.state
            for part in path.split("/"):
                value = value[part]
            console.print(f"{{path}} = {{value}}")
    else:
        console.print(composite.state)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
'''


def scaffold_workspace_in_place(
    *,
    workspace_root: Path,
    workspace_name: str,
    template_source: str | None = None,
    branch: str | None = None,
    package_path: str | None = None,
) -> Path:
    """Promote an existing git checkout into a viva-superpowers workspace.

    Implements the manual ritual the friction log called out: copies the
    template tree minus a small conflict set, renders the .j2 files,
    merges new dependencies into the existing pyproject, appends new
    .gitignore entries, runs the vivarium-workbench auto-pin, creates the
    Python package skeleton if absent, switches to a workspace branch,
    commits, and registers in the workspace catalog.

    Returns the workspace root on success.
    """
    if not workspace_root.is_dir():
        raise click.ClickException(
            f"--in-place target {workspace_root} is not an existing directory"
        )
    if not (workspace_root / ".git").exists():
        raise click.ClickException(
            f"{workspace_root} is not a git repo (no .git/) — --in-place "
            "promotes an existing checkout into a workspace branch and needs "
            "git to land its commit."
        )
    if (workspace_root / "workspace.yaml").exists():
        raise click.ClickException(
            f"{workspace_root} already has workspace.yaml — refusing to "
            "overlay the template on a workspace that already exists. "
            "Delete workspace.yaml first if you really want to re-bootstrap."
        )

    pkg_slug = package_path or f"pbg_{workspace_name.replace('-', '_')}"
    branch = branch or f"{workspace_root.name}-workspace"

    # Create + switch to the workspace branch. -B is intentional: re-running
    # in-place after a failure shouldn't error on "branch already exists" if
    # the user is iterating.
    try:
        subprocess.run(["git", "-C", str(workspace_root), "checkout", "-B", branch],
                       check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        raise click.ClickException(
            f"git checkout -B {branch} failed: {e.stderr.decode(errors='replace')}"
        )

    # Acquire viva-template into a temp dir; we copy from there selectively.
    src = _resolve_source(template_source)
    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp) / "viva-template-staging"
        _acquire(src, staging)

        # Copy every file in template/ EXCEPT the conflict set + template-init.sh.
        copied = 0
        for src_file in sorted(p for p in staging.rglob("*") if p.is_file()):
            rel = src_file.relative_to(staging)
            # Skip the conflict-set files at any nesting level
            if rel.name in _INPLACE_SKIP_FILES:
                continue
            out = workspace_root / rel
            # Don't clobber existing files at the same path (e.g. a workspace
            # that already has notes/README.md from a previous attempt).
            if out.exists():
                continue
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, out)
            copied += 1

        # Render all .j2 files in place (after copying — Python loop, not bash).
        subs = _inplace_render_subs(workspace_name, pkg_slug)
        for j2 in list(workspace_root.rglob("*.j2")):
            # scripts/ holds dashboard-runtime templates that must remain .j2
            if "scripts/" in str(j2.relative_to(workspace_root)) + "/":
                continue
            out = j2.with_suffix("")  # strip .j2
            out.write_text(_render_text(j2.read_text(encoding="utf-8"), subs))
            j2.unlink()

        # Merge pyproject.toml deps from the template's pyproject (which we
        # didn't copy because it'd clobber the existing one).
        template_pyproject = staging / "pyproject.toml.j2"
        added_deps: list[str] = []
        added_cli_extra = False
        added_cli_script = False
        if template_pyproject.is_file():
            tpl_text = _render_text(template_pyproject.read_text(encoding="utf-8"), subs)
            tpl_deps = _inplace_extract_template_deps(tpl_text)
            tpl_requires_python = _template_requires_python(tpl_text)
            existing_pyproject = workspace_root / "pyproject.toml"
            if existing_pyproject.is_file():
                added_deps = _inplace_merge_pyproject(existing_pyproject, tpl_deps)
                # Merged template deps may need a higher Python floor than the
                # host declares; raise it so uv lock/sync can resolve. (#357)
                _inplace_merge_requires_python(
                    existing_pyproject, tpl_requires_python)
                added_cli_extra = _inplace_merge_pyproject_cli_extra(existing_pyproject)
                added_cli_script = _inplace_merge_pyproject_scripts(
                    existing_pyproject, workspace_name, pkg_slug)
            else:
                # No pyproject at all — write the rendered template wholesale,
                # then add the cli extra + [project.scripts] entry through the
                # same helpers as the merge path. The template's own
                # pyproject.toml.j2 does not declare them, so relying on the
                # wholesale copy left the in-place path without cli wiring.
                existing_pyproject.write_text(tpl_text)
                added_deps = list(tpl_deps)
                added_cli_extra = _inplace_merge_pyproject_cli_extra(existing_pyproject)
                added_cli_script = _inplace_merge_pyproject_scripts(
                    existing_pyproject, workspace_name, pkg_slug)

        # Merge .gitignore lines.
        template_gitignore = staging / ".gitignore"
        added_ignore_lines = 0
        if template_gitignore.is_file():
            tpl_lines = template_gitignore.read_text(encoding="utf-8").splitlines()
            added_ignore_lines = _inplace_merge_gitignore(
                workspace_root / ".gitignore", tpl_lines)

        # vivarium-workbench auto-pin (only if a sibling checkout exists).
        autopin_path = None
        pyproject = workspace_root / "pyproject.toml"
        if pyproject.is_file():
            autopin_path = _inplace_autopin_vivarium(pyproject)

        # Python package skeleton.
        pkg_created = _inplace_create_pkg(workspace_root, pkg_slug)
        cli_created = _inplace_create_cli(workspace_root, workspace_name, pkg_slug)

    # Commit so the workspace branch has a single bootstrap commit.
    try:
        subprocess.run(["git", "-C", str(workspace_root), "add", "-A"],
                       check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(workspace_root), "commit",
             "-m", f"feat: workspace bootstrap (viva-superpowers --in-place)\n\n"
                   f"Promoted to workspace via scaffold_workspace_in_place "
                   f"from viva-template. Branch: {branch}. Package: {pkg_slug}."],
            check=True, capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode(errors='replace') if e.stderr else ""
        # "nothing to commit" is benign — likely a re-run; surface but don't fail.
        if "nothing to commit" in stderr:
            click.echo("note: git commit found nothing to add (re-run on a "
                       "clean workspace?)", err=True)
        else:
            raise click.ClickException(f"git commit failed: {stderr}")

    # Register in the workspace catalog so /viva-navigate status etc. find this workspace.
    try:
        from . import workspace_catalog
        workspace_catalog.add(workspace_root, name=workspace_name)
    except Exception as e:
        click.echo(f"warning: workspace_catalog.add failed: {e}", err=True)

    # Print a concise summary.
    click.echo(f"  branch:    {branch}")
    click.echo(f"  package:   {pkg_slug}{' (created)' if pkg_created else ' (existing)'}")
    click.echo(f"  cli:       {pkg_slug}.cli{' (created)' if cli_created else ' (existing)'}")
    click.echo(f"  files copied from template: {copied}")
    if added_deps:
        click.echo(f"  deps added to pyproject.toml: {', '.join(added_deps)}")
    if added_cli_extra:
        click.echo("  pyproject.toml: added [project.optional-dependencies] cli extra")
    if added_cli_script:
        click.echo(f"  pyproject.toml: added [project.scripts] entry '{workspace_name}'")
    if added_ignore_lines:
        click.echo(f"  .gitignore lines added: {added_ignore_lines}")
    if autopin_path:
        click.echo(f"  vivarium-workbench auto-pinned to: {autopin_path}")
    elif added_deps and any("vivarium-workbench" in d for d in added_deps):
        click.echo(
            "  note: vivarium-workbench added to deps but no sibling "
            "../vivarium-workbench checkout found for auto-pin. "
            "Set VIVARIUM_WORKBENCH_PATH or add [tool.uv.sources] manually "
            "before `uv pip install -e \".[dev]\"`.",
            err=True,
        )
    return workspace_root


@click.group()
def cli() -> None:
    pass


def _normalize_workspace_name(raw: str) -> str:
    """Strip a leading `pbg-` / `pbg_` prefix from the workspace name.

    The downstream template-init produces a python package `pbg_<name>`. If
    the user already prefixed the name with `pbg-` (perfectly natural — every
    sibling `pbg-*` repo is named that way), we'd end up with `pbg_pbg_<rest>`.
    Strip the prefix instead and emit a warning so the user is aware.
    """
    stripped = raw
    for prefix in ("pbg-", "pbg_"):
        if stripped.lower().startswith(prefix):
            stripped = stripped[len(prefix):]
            click.echo(
                f"warning: --name '{raw}' starts with '{prefix}'; using "
                f"'{stripped}' so the python package is pbg_{stripped} "
                "(not pbg_pbg_…). Pass --name without the pbg- prefix to "
                "silence this warning.",
                err=True,
            )
            break
    return stripped


@cli.command()
@click.option("--name", required=True, help="Workspace name (without pbg- prefix; the python package will be pbg_<name>)")
@click.option("--target", required=True, type=click.Path(path_type=Path), help="Target directory (must not exist or be empty)")
@click.option("--template-source", default=None, help="Path or git URL of viva-template (default: $VIVA_TEMPLATE (or $PBG_TEMPLATE) or upstream)")
@click.option("--in-place", "in_place", is_flag=True, default=False,
              help="Promote an existing git checkout into a workspace branch (see /viva-workspace --in-place docs).")
@click.option("--branch", default=None, help="Branch name for --in-place mode (default: <repo-name>-workspace).")
@click.option("--package", "package_path", default=None,
              help="Python package path for --in-place mode (default: pbg_<repo-name-normalized>).")
def workspace(name: str, target: Path, template_source: str | None,
              in_place: bool, branch: str | None, package_path: str | None) -> None:
    if in_place:
        name = _normalize_workspace_name(name)
        out = scaffold_workspace_in_place(
            workspace_root=target.resolve(),
            workspace_name=name,
            template_source=template_source,
            branch=branch,
            package_path=package_path,
        )
        click.echo(f"workspace promoted in-place at {out}")
        return
    name = _normalize_workspace_name(name)
    out = scaffold_workspace(target, name, template_source)
    click.echo(f"workspace scaffolded at {out}")


_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def _render_text(text: str, substitutions: dict) -> str:
    """Replace `{{ key }}` (with or without internal whitespace) using substitutions dict."""
    return _PLACEHOLDER.sub(lambda m: str(substitutions.get(m.group(1), m.group(0))), text)


def _render_template_tree(src: Path, dst: Path, substitutions: dict) -> None:
    """Copy src → dst rendering .j2 files. .keep files stay as empty markers."""
    if dst.exists():
        if not dst.is_dir():
            raise click.ClickException(f"{dst} exists and is not a directory")
        if any(dst.iterdir()):
            raise click.ClickException(f"{dst} exists and is non-empty")
    dst.mkdir(parents=True, exist_ok=True)
    for src_file in sorted(p for p in src.rglob("*") if p.is_file()):
        rel = src_file.relative_to(src)
        out_rel = Path(str(rel).removesuffix(".j2"))
        out_path = dst / out_rel
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if src_file.suffix == ".j2":
            out_path.write_text(_render_text(src_file.read_text(encoding="utf-8"), substitutions))
        else:
            shutil.copy2(src_file, out_path)


def _rename_placeholder_pkg(target: Path, slug: str) -> None:
    """Rename the literal `viva__model__` placeholder dir to `viva_<slug>`.

    The shipped template dir must avoid `<`/`>` — those are reserved characters
    in native Windows filenames, so a bracketed name (`viva_<model>`) fails to
    extract from the wheel on Windows with os error 123 before scaffolding ever
    runs (issue #202). `viva__model__` is Windows-legal and unambiguous.
    """
    placeholder = target / "viva__model__"
    if placeholder.exists():
        placeholder.rename(target / f"viva_{slug}")


@cli.command()
@click.option("--model-name", required=True, help="Human-readable model name (e.g. ecoli-replication)")
@click.option("--model-slug", required=True, help="Python-importable slug (e.g. ecoli_replication)")
@click.option("--target", required=True, type=click.Path(path_type=Path),
              help="Target directory (must not exist or be empty)")
def model(model_name: str, model_slug: str, target: Path) -> None:
    from ._resources import resource_dir
    src = resource_dir("templates") / "model"
    if not src.is_dir():
        raise click.ClickException(f"model template missing at {src}")
    _render_template_tree(src, target, {
        "model_name": model_name,
        "model_slug": model_slug,
    })
    _rename_placeholder_pkg(target, model_slug)
    click.echo(f"model scaffolded at {target}")


@cli.command("import-model")
@click.option("--workspace", required=True, type=click.Path(path_type=Path),
              help="Workspace root (must contain workspace.yaml).")
@click.option("--name", required=True, help="Catalog name for this import.")
@click.option("--source", required=True, help="Git URL or local path of the external repo.")
@click.option("--ref", default="main", help="Git ref (tag, branch, commit) to pin (default: main).")
@click.option("--mode", required=True,
              type=click.Choice(["reference", "fork-source", "in-place"]),
              help="reference (read-only), fork-source (catalog only), or in-place (submodule under models/).")
@click.option("--description", default=None, help="Optional human-readable description.")
def import_model(workspace: Path, name: str, source: str, ref: str,
                 mode: str, description: str | None) -> None:
    """Register an external model in the workspace's imports catalog.

    For mode='reference': also adds <source> as a submodule under external/<name>/
    (or copies it if <source> is a local path).

    For mode='fork-source': only registers the catalog entry; no checkout happens
    until /pbg-add-model --from-import <name> consumes it.

    For mode='in-place': adds <source> as a submodule under models/<name>/ and
    marks the model entry external=true. Use this when you want to operate
    on an existing model repo without forking.
    """
    from .imports import register_import
    from .workspace_yaml import load_workspace, save_workspace

    ws = workspace.resolve()
    if not (ws / "workspace.yaml").exists():
        raise click.ClickException(f"workspace.yaml missing at {ws}")

    # Compute the path on disk (mode-dependent)
    if mode == "reference":
        path = f"external/{name}"
    elif mode == "in-place":
        path = f"models/{name}"
    else:
        path = None

    # 1. Register in catalog (validates schema)
    register_import(
        ws, name=name, source=source, ref=ref, mode=mode,
        path=path, description=description,
    )

    # 2. For reference + in-place modes: actually add the submodule
    if mode in ("reference", "in-place"):
        target_dir = ws / path
        if target_dir.exists():
            click.echo(f"target {path} already exists; skipping submodule add", err=True)
        else:
            target_dir.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["git", "-C", str(ws),
                 "-c", "protocol.file.allow=always",
                 "submodule", "add", source, path],
                check=True,
            )
            # Pin to ref
            subprocess.run(
                ["git", "-C", str(target_dir), "checkout", ref],
                check=True,
            )

    # 3. For in-place mode: also mark the model as external in workspace.yaml.models
    if mode == "in-place":
        ws_data = load_workspace(ws / "workspace.yaml")
        models = ws_data.setdefault("models", {})
        models[name] = {
            "submodule_path": path,
            "remote": source,
            "pbg_processes": [],
            "stages": {"add_model": {"status": "complete", "pr": None, "completed": "2026-05-09"}},
            "external": True,
        }
        save_workspace(ws / "workspace.yaml", ws_data)

    click.echo(f"import '{name}' registered (mode={mode})")


# ---------------------------------------------------------------------------
# investigation-from-wrapper — scaffold an investigation + skeleton studies
# from a list of composite-generator names (Task 14).
#
# Emits the "canonical" shapes hand-authored in the viva-fenics build:
#   investigations/<slug>/investigation.yaml  (schema_version 2)
#   studies/<study-slug>/study.yaml           (schema_version 4, one per
#     generator, wired into a linear pipeline_gate chain)
#
# The goal is that wrapping a batch of composite generators into a showcase
# investigation becomes a one-liner; the author then fills in the
# expected_behavior / behavior_tests placeholders and writes sims/run.py per
# study.
# ---------------------------------------------------------------------------


_SLUG_SEGMENT_RE = re.compile(r"[^a-z0-9]+")


def _kebab(s: str) -> str:
    """Lowercase + non-alnum runs -> single hyphen, trimmed."""
    return _SLUG_SEGMENT_RE.sub("-", s.strip().lower()).strip("-")


def _study_slug_for_generator(generator: str, used: set[str]) -> str:
    """Derive a study slug from a (possibly dotted) generator id/name.

    Uses the last dotted segment (e.g. ``pkg.composites.poisson.poisson_baseline``
    -> ``poisson-baseline``; a bare short name like ``poisson_baseline`` maps
    the same way). Collisions (two generators whose last segment kebab-cases
    to the same slug) are disambiguated with a ``-2``, ``-3``, ... suffix.
    """
    last = generator.strip().split(".")[-1]
    base = _kebab(last) or "study"
    slug = base
    n = 2
    while slug in used:
        slug = f"{base}-{n}"
        n += 1
    used.add(slug)
    return slug


def _investigation_from_wrapper_study_stub(
    slug: str, generator: str, prev_slug: str | None,
) -> dict:
    """A skeleton study.yaml dict (schema_version 4) for one generator.

    Mirrors the shape hand-authored + reviewed in the viva-fenics build
    (``studies/poisson-validation/study.yaml``): baseline -> a single
    composite entry, expected_behavior/behavior_tests carrying the
    en/measure/expect grammar (stubbed), a pipeline_gate wiring a linear
    chain across the batch, and a canonical_runs default entry pointing at
    ``studies/<slug>/sims/run.py`` — ``script:`` is resolved relative to the
    WORKSPACE ROOT by ``/viva-study run-script``, not the study dir (see
    ``docs/concepts/vivarium-workbench-model.md``), matching every
    hand-authored viva-fenics reference study.
    """
    import datetime as _dt

    today = _dt.date.today().isoformat()
    behavior_name = f"{slug}-behaves"
    prerequisites = (
        [{"study": prev_slug, "condition": "tests-passed", "relation": "builds-on"}]
        if prev_slug else []
    )
    return {
        "schema_version": 4,
        "name": slug,
        "title": slug.replace("-", " ").capitalize(),
        "created": today,
        # Two-axis ontology (viva-template #70, schema stays v4, additive):
        # `execution_status` tracks run lifecycle, `verdict` tracks the scientific
        # conclusion. A negative result is completed + refuted, never failed.
        # Legacy `status` is kept for back-compat.
        "status": "planned",
        "execution_status": "planned",
        "verdict": None,
        "phase": "Design",
        "question": "TBD — what question does this study answer?",
        "experiment": f"TBD — describe what {generator} is expected to demonstrate.",
        "description": f"Wraps the `{generator}` composite generator as a study. Fill in.",
        "baseline": [{
            "name": "baseline",
            "composite": generator,
            "params": {},
        }],
        "variants": [],
        "behavior_tests": [{
            "name": behavior_name,
            "classification": "primary",
            "description": "TBD — plain-language statement of the expected behavior.",
            "measure": {"kind": "derived_scalar", "field": "TBD"},
            "pass_if": {"op": "<", "value": None},
            "requires_simulation": "baseline",
        }],
        "pipeline_gate": {
            "prerequisites": prerequisites,
            "enables": [],
        },
        "canonical_runs": [{
            "name": "default",
            "script": f"studies/{slug}/sims/run.py",
            "args": [],
            "label": slug,
            "default": True,
        }],
        # Three-view standard (World / Mechanism / Decision). The Decision view
        # is a PHASE MAP over a swept grid — PASS and FAILURE regions — not a
        # single representative run (enrichment-plan principle 1). Fill in the
        # address/render for each once sims/run.py produces the data + grid; the
        # report_linter's `decision_viz_single_run` nudge flags a Decision view
        # that is still a single run.
        "visualizations": [
            {
                "name": f"{slug}-world",
                "view": "world",
                "status": "stub",
                "description": "Fill in: the environment/state the study runs in.",
            },
            {
                "name": f"{slug}-mechanism",
                "view": "mechanism",
                "status": "stub",
                "description": "Fill in: the mechanism/trajectory that drives the result.",
            },
            {
                "name": f"{slug}-decision",
                "view": "decision",
                "phase_map": True,
                "grid_artifact": f"studies/{slug}/grid/phase_map.parquet",
                "status": "stub",
                "description": (
                    "Fill in: phase map identifying PASS and FAILURE regions across "
                    "a swept grid (not a single run). Built by sims/run.py's "
                    "grid sweep; persisted to grid_artifact."
                ),
            },
        ],
        # Findings carry provenance on every quantitative claim (quality-bar
        # item 4): each authored finding should fill expected.cites (→
        # references/papers.bib) or an expert_reference. The report_linter's
        # `finding_missing_cites` nudge flags an uncited quantitative finding.
        "findings": [],
        # derive-confidence / gate wiring. `confidence:` is the graph-node badge;
        # it must match study_verdict.derive_confidence(spec) — at Design that is
        # "Planned". Advance it (and `report.verdict`) from real evidence at
        # Decide; the `authored_confidence_diverges` nudge flags a mismatch.
        "confidence": "Planned",
        "report": {"verdict": None},
        "result": None,
    }


def _investigation_from_wrapper_stub(
    inv_slug: str, name: str, study_slugs: list[str], acceptance_criteria: list[dict],
) -> dict:
    """A skeleton investigation.yaml dict (schema_version 2).

    Mirrors ``investigations/fenics-showcase/investigation.yaml``.
    """
    import datetime as _dt

    today = _dt.date.today().isoformat()
    return {
        "schema_version": 2,
        "name": inv_slug,
        "title": name.replace("-", " ").capitalize(),
        "created": today,
        "status": "planning",
        "question": "TBD — what overarching question ties these studies together?",
        "hypothesis": "TBD — what do we expect the composed studies to show?",
        "description": (
            f"Scaffolded from {len(study_slugs)} composite generator(s) via "
            "`investigation-from-wrapper`. Fill in the narrative."
        ),
        "studies": list(study_slugs),
        "executive": {
            "what_is_this": "TBD — one paragraph, for a reader who has never seen this.",
            "verdict": "TBD — fill in once studies are run.",
            "verdict_status": "in-progress",
            "verdict_detail": "TBD",
            "decisions_needed": [],
        },
        "scientific_argument": {
            "main_claim": "TBD — the one-sentence claim this investigation supports.",
            "evidence_for": [],
            "evidence_against": [],
            "key_figures": [],
            "caveats": [],
        },
        "acceptance_criteria": acceptance_criteria,
    }


def scaffold_investigation_from_wrapper(
    workspace: Path,
    name: str,
    studies: list[str],
    *,
    investigation_slug: str | None = None,
    force: bool = False,
) -> dict:
    """Write an investigation + one skeleton study per composite generator.

    ``studies`` is the list of composite generator ids/short names (as given
    by the caller — the raw string is written verbatim into each study's
    ``baseline[0].composite``). Study slugs are derived from each generator's
    last dotted segment, kebab-cased, deduplicated on collision.

    Existing ``investigation.yaml`` / ``study.yaml`` files are never
    overwritten unless ``force=True`` — a collision is reported (not raised)
    so a partial re-run is safe.

    Returns a summary dict: ``{investigation_path, investigation_written,
    studies: [{slug, generator, path, written}]}``.
    """
    from . import study_io
    from .workspace_paths import WorkspacePaths

    if not studies:
        raise click.ClickException("--studies must list at least one composite generator")

    ws = Path(workspace).resolve()
    wp = WorkspacePaths.load(ws)
    inv_slug = investigation_slug or _kebab(name) or name

    used_slugs: set[str] = set()
    study_slugs: list[str] = []
    for gen in studies:
        gen = gen.strip()
        if not gen:
            continue
        study_slugs.append(_study_slug_for_generator(gen, used_slugs))

    study_results: list[dict] = []
    acceptance_criteria: list[dict] = []
    prev_slug: str | None = None
    for slug, gen in zip(study_slugs, [s.strip() for s in studies if s.strip()]):
        study_dir = wp.studies / slug
        study_path = study_dir / "study.yaml"
        stub = _investigation_from_wrapper_study_stub(slug, gen, prev_slug)
        acceptance_criteria.append({
            "study": slug,
            "behavior": stub["behavior_tests"][0]["name"],
        })
        written = False
        if study_path.exists() and not force:
            click.echo(f"skip: {study_path} already exists (use --force to overwrite)")
        else:
            study_dir.mkdir(parents=True, exist_ok=True)
            study_io.save_yaml_atomic(study_path, stub)
            written = True
            click.echo(f"wrote: {study_path}")
        study_results.append({
            "slug": slug, "generator": gen, "path": str(study_path), "written": written,
        })
        prev_slug = slug

    inv_dir = wp.investigations / inv_slug
    inv_path = inv_dir / "investigation.yaml"
    inv_written = False
    if inv_path.exists() and not force:
        click.echo(f"skip: {inv_path} already exists (use --force to overwrite)")
    else:
        inv_dir.mkdir(parents=True, exist_ok=True)
        inv_stub = _investigation_from_wrapper_stub(
            inv_slug, name, study_slugs, acceptance_criteria,
        )
        study_io.save_yaml_atomic(inv_path, inv_stub)
        inv_written = True
        click.echo(f"wrote: {inv_path}")

    click.echo(
        f"\n{len(study_results)} stud{'y' if len(study_results) == 1 else 'ies'} + "
        f"1 investigation scaffolded for '{inv_slug}'.\n"
        "Next: fill in expected_behavior/behavior_tests + author sims/run.py "
        "per study (studies/<slug>/sims/run.py)."
    )

    return {
        "investigation_path": str(inv_path),
        "investigation_written": inv_written,
        "studies": study_results,
    }


@cli.command("investigation-from-wrapper")
@click.option("--name", required=True, help="Investigation name (used to derive the slug and title).")
@click.option("--studies", required=True,
              help="Comma-separated composite generator ids/names, e.g. "
                   "'pkg.composites.a.a_baseline,pkg.composites.b.b_baseline'.")
@click.option("--workspace", "workspace_", default=".", type=click.Path(path_type=Path),
              help="Workspace root (must contain workspace.yaml). Default: current dir.")
@click.option("--investigation-slug", "investigation_slug", default=None,
              help="Directory slug for the investigation (default: kebab-cased --name).")
@click.option("--force", is_flag=True, default=False,
              help="Overwrite existing investigation.yaml/study.yaml files.")
def investigation_from_wrapper(
    name: str, studies: str, workspace_: Path,
    investigation_slug: str | None, force: bool,
) -> None:
    """Scaffold an investigation + one skeleton study per composite generator."""
    ws = workspace_.resolve()
    if not (ws / "workspace.yaml").exists():
        raise click.ClickException(f"workspace.yaml missing at {ws}")
    gen_list = [s for s in (studies.split(",")) if s.strip()]
    scaffold_investigation_from_wrapper(
        ws, name, gen_list,
        investigation_slug=investigation_slug, force=force,
    )


if __name__ == "__main__":
    cli()
