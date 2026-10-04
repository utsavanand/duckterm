"""Make announcement text speakable by Kokoro without dropping words.

Kokoro's English front end (misaki) silently drops words that aren't in its
dictionary unless a GPL espeak fallback is installed, which DuckTerm never
installs. On 2026-09-29 that turned "main-dev" into "main" and made
"duckterm", "qa" and "utsava.xyz" vanish. Session names are the point of an
announcement, so every word here is kept: known words as they are, a few
common ones with an inline pronunciation, compounds split into known halves,
and anything else spelled out letter by letter.

Stdlib only: the worker imports this by path, and tests pass a fake `known`.
"""

import re
from collections.abc import Callable

# Words agents use in names that misaki doesn't know, in misaki's phoneme
# notation, written as its inline pronunciation markup: [word](/phonemes/).
PRONUNCIATIONS = {
    "dev": "dˈɛv",
    "devs": "dˈɛvz",
    "config": "kˈɑnfɪɡ",
    "frontend": "fɹˈʌntˌɛnd",
    "kokoro": "kəkˈɔɹO",
}

_TOKEN = re.compile(r"[A-Za-z]+|\d+|\s+|.", re.DOTALL)
_MIN_PART = 3


def _word(word: str, known: Callable[[str], bool]) -> str:
    lower = word.lower()
    if lower in PRONUNCIATIONS:
        return f"[{word}](/{PRONUNCIATIONS[lower]}/)"
    if len(word) <= 3 and not known(lower):
        return word.upper()  # qa, ui: misaki spells capitals as letters
    if known(word) or known(lower):
        return word
    for cut in range(_MIN_PART, len(lower) - _MIN_PART + 1):
        if known(lower[:cut]) and known(lower[cut:]):
            return f"{lower[:cut]} {lower[cut:]}"
    return word.upper()


def speakable(text: str, known: Callable[[str], bool]) -> str:
    """The same announcement with every word pronounceable. Separators in
    names become spaces, a dot between words is read as "dot"."""
    out: list[str] = []
    tokens = _TOKEN.findall(text)
    for i, token in enumerate(tokens):
        prev = tokens[i - 1] if i else ""
        nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
        if token.isalpha():
            out.append(_word(token, known))
        elif token.isdigit():
            out.append((" " if prev.isalpha() else "") + token)  # v2 -> "V 2"
        elif token.isspace():
            out.append(token)
        elif token == "." and (prev.isalpha() or nxt.isalpha()) and prev.strip() and nxt.strip():
            out.append(" dot ")  # utsava.xyz; a decimal like 3.9 keeps its point
        elif token in "-_/":
            out.append(" ")
        else:
            out.append(token)
    return re.sub(r" {2,}", " ", "".join(out)).strip()
