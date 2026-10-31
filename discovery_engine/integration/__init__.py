"""Workspace / server integration layer (NOT part of the discovery core).

Contains the viva-template scaffolder, the workbench server preflight, and the
``~/.pbg`` server registry. Core discovery modules never import this package;
it is reached only from the CLI entry points and (lazily, optionally) the
linter's live-server probes.
"""
