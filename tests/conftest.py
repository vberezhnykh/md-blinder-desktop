"""Общие настройки тестов: шрифты IBM Plex и стили приложения, как при обычном запуске."""

import pytest


@pytest.fixture(scope="session", autouse=True)
def _app_style(qapp):
    from md_blinder.ui.theme import STYLESHEET, register_fonts

    register_fonts()
    qapp.setStyle("Fusion")
    qapp.setStyleSheet(STYLESHEET)
    yield
