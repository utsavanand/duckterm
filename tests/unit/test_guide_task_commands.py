"""The session guide is how agents learn to report Folder Tasks (product,
2026-10-02: the Tasks widget stayed empty). Every task command it shows must
parse with the real CLI, so the guide can't drift from the commands."""

import re
import shlex

from duckterm.cli import build_parser
from duckterm.helpers.session_instructions import GUIDE


def test_every_task_command_in_the_guide_parses() -> None:
    commands = re.findall(r"`(duckterm session task [^`]+)`", GUIDE)
    assert {shlex.split(c)[3] for c in commands} == {"start", "update", "handoff"}
    for command in commands:
        argv = [a.replace("<title>", "Fix B16").replace("ID", "t1") for a in shlex.split(command)]
        args = build_parser().parse_args(argv[1:])
        assert args.task_action == argv[3]


def test_guide_limits_build_start_authority_to_product_and_architect() -> None:
    # Owner rule (2026-10-05): product/architect assignments may START work,
    # but never stand in for the owner on previews, releases, schema or pushes.
    assert "product or\narchitect session carries the owner's authority to START" in GUIDE
    for gate in ("previewed", "low-risk rule", "schema", "security", "pushes"):
        assert gate in GUIDE
