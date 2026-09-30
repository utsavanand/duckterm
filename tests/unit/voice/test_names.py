import pytest

from duckterm.voice.names import speakable

# Words misaki knew on 2026-09-29, from probing it on the owner's Mac.
KNOWN = {
    "architect",
    "needs",
    "your",
    "input",
    "is",
    "complete",
    "main",
    "in",
    "nourish",
    "release",
    "feature",
    "remote",
    "session",
    "connectors",
    "duck",
    "term",
    "three",
    "sessions",
    "need",
    "you",
    "preptrack",
    "prep",
    "track",
    "repo",
    "docs",
    "oracle",
}


def known(word: str) -> bool:
    return word.lower() in KNOWN


# The failures observed without espeak: each word must survive.
@pytest.mark.parametrize(
    ("text", "spoken"),
    [
        ("main-dev is complete", "main [dev](/dˈɛv/) is complete"),
        ("duckterm needs your input", "duck term needs your input"),
        ("main-qa needs your input", "main QA needs your input"),
        ("utsava.xyz is complete", "UTSAVA dot XYZ is complete"),
        ("feature-remote-session is complete", "feature remote session is complete"),
        ("release_dev needs your input", "release [dev](/dˈɛv/) needs your input"),
        ("three sessions need you", "three sessions need you"),
        ("PrepTrack needs your input", "PrepTrack needs your input"),
        ("sotto is complete", "SOTTO is complete"),
    ],
)
def test_no_word_is_dropped(text: str, spoken: str) -> None:
    assert speakable(text, known) == spoken


def test_version_dots_and_numbers_survive() -> None:
    assert speakable("repo v2 is complete", known) == "repo V 2 is complete"
    assert speakable("docs 3.9 is complete", known) == "docs 3.9 is complete"
