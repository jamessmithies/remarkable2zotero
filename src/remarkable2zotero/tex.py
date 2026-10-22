from __future__ import annotations

import re

# Old TeX PDFs (dvips, OT1-encoded fonts) put ligatures at control-character positions,
# which the tablet copies into highlight text as-is
LIGATURES = str.maketrans(
    {
        "\x0b": "ff",
        "\x0c": "fi",
        "\x0d": "fl",
        "\x0e": "ffi",
        "\x0f": "ffl",
    }
)

# OT1 also puts curly double quotes at '\' and '"', and dashes at '{' and '|'
PUNCTUATION = str.maketrans({"\\": "“", '"': "”", "{": "–", "|": "—"})

_TEX_SIGNS = re.compile(r"[\x0b-\x0f]|\\[A-Za-z]")

# The kern before 'j' leaves a gap the tablet reads as a space: 'ob ject', 'pro jected'.
# No word boundary before the prefix: the tablet may have glued it to the word before
# at a line break ('Thatpro jection'), which respacing separates later
_SPLIT_J = re.compile(r"(ob|pro|sub) (j[a-z])")


def is_tex_encoded(texts: list[str]) -> bool:
    """Whether a document's text shows signs of OT1 encoding: ligature control
    characters, or '\\' standing in for an opening quote."""
    return any(_TEX_SIGNS.search(t) for t in texts)


def decode(text: str, tex_encoded: bool) -> str:
    """Turn OT1 ligatures into letters, and with tex_encoded, its quotes and dashes
    into punctuation and words split before a 'j' back into one."""
    text = text.translate(LIGATURES)
    if not tex_encoded:
        return text
    return _SPLIT_J.sub(r"\1\2", text.translate(PUNCTUATION))
