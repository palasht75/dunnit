from dunnit.lines import probably_in_string


def _pos(line: str, needle: str) -> int:
    return line.index(needle)


def test_marker_in_double_quoted_string():
    line = 'msg = "TODO user-facing"'
    assert probably_in_string(line, _pos(line, "TODO")) is True


def test_marker_in_single_quoted_string():
    line = "msg = 'TODO user-facing'"
    assert probably_in_string(line, _pos(line, "TODO")) is True


def test_marker_in_comment_is_live():
    line = "# TODO implement this"
    assert probably_in_string(line, _pos(line, "TODO")) is False


def test_apostrophe_does_not_toggle():
    line = "# don't forget: TODO implement"
    assert probably_in_string(line, _pos(line, "TODO")) is False


def test_escaped_quote_does_not_toggle():
    # the file content is: w("a \"quoted\" TODO") — escaped quotes stay inside
    line = 'w("a \\"quoted\\" TODO")'
    assert probably_in_string(line, _pos(line, "TODO")) is True
