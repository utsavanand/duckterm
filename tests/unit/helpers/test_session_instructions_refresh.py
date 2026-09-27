import hashlib

from duckterm.helpers.session_instructions import GUIDE, introduction, refresh_guides


def test_refresh_rewrites_only_stale_instruction_files(tmp_path) -> None:
    introduction("fresh", home=tmp_path)
    introduction("old", home=tmp_path)
    folder = tmp_path / "session-instructions"
    stale = folder / hashlib.sha256(b"old").hexdigest() / "collaboration.md"
    stale.write_text("Questions expire after five minutes by default.")

    assert refresh_guides(tmp_path) == 1
    assert stale.read_text() == GUIDE
    assert refresh_guides(tmp_path) == 0
    assert refresh_guides(tmp_path / "missing") == 0
