from metasphere.telegram.handler import _reply_to_line


def test_no_reply_gives_empty():
    assert _reply_to_line({"text": "hi"}) == ""


def test_reply_quotes_first_chars_on_one_line():
    msg = {"reply_to_message": {"text": "Sonde channels · go where\nbuyers post " + "x" * 200}}
    line = _reply_to_line(msg, limit=40)
    assert line.startswith('↩ replying to: "Sonde channels · go where buyers post')
    assert line.endswith('..."')
    assert "\n" not in line


def test_reply_to_caption_only():
    assert _reply_to_line({"reply_to_message": {"caption": "photo note"}}) == '↩ replying to: "photo note"'
