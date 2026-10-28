"""Minimal valid pbg-* wrapper for tests."""
from .processes import FakeProcess


def register_with(core):
    core.register_link("FakeProcess", FakeProcess)
