"""Versioned package prompts; branching and byte budgets stay in Python."""

from functools import lru_cache
from importlib.resources import files
from string import Template


@lru_cache(maxsize=32)
def load(name: str) -> str:
    if not name or any(c not in "abcdefghijklmnopqrstuvwxyz_" for c in name):
        raise ValueError("Invalid prompt name")
    return files(__package__).joinpath(name + ".txt").read_text(encoding="utf-8")


def render(name: str, **values: str) -> str:
    return Template(load(name)).safe_substitute(values)
