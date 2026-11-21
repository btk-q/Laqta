import sys
import threading

from laqta.ui import problem


def test_describe_is_one_plain_line():
    assert problem.describe(ValueError("bad value\nmore detail")) == "ValueError: bad value"
    assert problem.describe(OSError()) == "OSError"
    assert len(problem.describe(RuntimeError("x" * 900))) < 230


def test_errors_are_logged_and_shown_once(monkeypatch, caplog):
    shown = []
    old_hook, old_thread_hook = sys.excepthook, threading.excepthook
    problem._shown.clear()
    try:
        problem.install(lambda fn: fn())
        monkeypatch.setattr(problem, "show", lambda error, fatal=False: shown.append(type(error).__name__))
        for error in (ValueError("one"), KeyError("two")):
            try:
                raise error
            except Exception as e:
                sys.excepthook(type(e), e, e.__traceback__)
        worker = threading.Thread(target=lambda: 1 / 0)
        worker.start()
        worker.join()
        assert shown == ["ValueError"]  # once per run, however many follow
        assert caplog.text.count("unexpected error") == 3  # but every one is in the log
    finally:
        sys.excepthook, threading.excepthook = old_hook, old_thread_hook
        problem._shown.clear()
