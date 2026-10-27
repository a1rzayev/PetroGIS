import threading

from petrogis.ui.workers import TaskRunner


def test_result_delivered_on_ui_thread(qtbot):
    runner = TaskRunner()
    main_thread = threading.get_ident()
    seen = {}

    def work(a, b):
        seen["worker_thread"] = threading.get_ident()
        return a + b

    def on_result(value):
        seen["value"] = value
        seen["callback_thread"] = threading.get_ident()

    with qtbot.waitSignal(runner.idle, timeout=5000):
        runner.submit(work, 2, b=3, on_result=on_result)
    assert seen["value"] == 5
    assert seen["worker_thread"] != main_thread
    assert seen["callback_thread"] == main_thread
    assert runner.pending == 0


def test_errors_forwarded(qtbot):
    runner = TaskRunner()
    errors = []

    def boom():
        raise ValueError("bad file")

    with qtbot.waitSignal(runner.idle, timeout=5000):
        runner.submit(boom, on_error=errors.append)
    assert len(errors) == 1 and str(errors[0]) == "bad file"
