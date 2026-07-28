"""Line-level heuristics shared by the diff checks."""

from __future__ import annotations

import re

# String delimiters, with two exclusions: backslash-escaped quotes (an
# escaped quote inside a string must not toggle string state) and
# apostrophes inside a word ("don't" is not a delimiter).
_DOUBLE_QUOTE = re.compile(r'(?<!\\)"')
_SINGLE_QUOTE = re.compile(r"(?<!\\)(?:(?<!\w)'|'(?!\w))")


def probably_in_string(line: str, pos: int) -> bool:
    """True when the match at ``pos`` likely sits inside a string literal.

    An odd number of quote characters before the match means the marker is
    probably data, not live code — e.g. a test fixture that *writes*
    ``@pytest.mark.skip`` into a temp file, or a user-facing message that
    happens to contain "TODO". Deliberately crude: it trades a few false
    negatives for not flagging the verifier's own test suite.
    """
    head = line[:pos]
    if len(_DOUBLE_QUOTE.findall(head)) % 2 == 1:
        return True
    return len(_SINGLE_QUOTE.findall(head)) % 2 == 1
