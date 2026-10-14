import gc

from md_blinder.ui import gc_guard


def test_gc_runs_only_from_main_thread_timer(qapp, monkeypatch):
    was_enabled = gc.isenabled()
    calls = []
    monkeypatch.setattr(gc, "collect", lambda gen=2: calls.append(gen) or 0)
    monkeypatch.setattr(gc, "get_count", lambda: (10_000, 0, 0))
    guard = gc_guard.MainThreadGc(qapp, interval_ms=10)
    try:
        assert not gc.isenabled()
        assert guard.timer.isActive()
        guard.check()
        assert calls == [0]
    finally:
        guard.timer.stop()
        if was_enabled:
            gc.enable()
