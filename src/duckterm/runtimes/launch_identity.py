"""Assign only new native conversations, preserving explicit resume/fork commands."""

import uuid


def assign(argv: list[str]) -> tuple[list[str], str | None]:
    flags = {word.split("=", 1)[0] for word in argv[1:]}
    if flags & {
        "--resume",
        "-r",
        "--continue",
        "-c",
        "--fork-session",
        "--fork",
        "--connect",
        "--no-session-persistence",
        "--",
    }:
        return argv, None
    supplied = []
    for index, word in enumerate(argv[1:], 1):
        if word == "--session-id":
            if index + 1 >= len(argv):
                raise ValueError("--session-id requires a UUID")
            supplied.append(argv[index + 1])
        elif word.startswith("--session-id="):
            supplied.append(word.partition("=")[2])
    if supplied:
        if len(supplied) != 1:
            raise ValueError("Specify one conversation UUID")
        try:
            native_id = str(uuid.UUID(supplied[0]))
        except ValueError as exc:
            raise ValueError("--session-id requires a UUID") from exc
        argv = list(argv)
        for index, word in enumerate(argv):
            if word == "--session-id":
                argv[index + 1] = native_id
            elif word.startswith("--session-id="):
                argv[index] = "--session-id=" + native_id
        return argv, native_id
    native_id = str(uuid.uuid4())
    return [*argv, "--session-id", native_id], native_id
