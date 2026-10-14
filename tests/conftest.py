"""Общие настройки тестов: шрифты IBM Plex, стили и сборка мусора, как при обычном запуске."""

import gc

import pytest


@pytest.fixture(scope="session", autouse=True)
def _app_style(qapp):
    from md_blinder.ui.theme import STYLESHEET, register_fonts

    register_fonts()
    qapp.setStyle("Fusion")
    qapp.setStyleSheet(STYLESHEET)
    # Как в программе: сборщик не должен запускаться в рабочем потоке обработки
    # и удалять там окна из предыдущих тестов (ui/gc_guard.py).
    gc.disable()
    yield
    gc.enable()


@pytest.fixture(autouse=True)
def _collect_in_main_thread():
    yield
    gc.collect()
