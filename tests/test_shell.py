"""Пункт в меню проводника, разбор командной строки и передача файла открытой программе."""

import uuid
from pathlib import Path

from md_blinder import shell
from md_blinder.ui.single_instance import InstanceServer, send_to_running


class FakeRegistry:
    """Минимальная замена winreg: ключи — пути, значения — словари."""

    HKEY_CURRENT_USER = "HKCU"
    KEY_WRITE = 0x20006
    REG_SZ = 1

    def __init__(self):
        self.keys: dict[str, dict] = {}

    class _Key:
        def __init__(self, values):
            self.values = values

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def CreateKeyEx(self, root, path, reserved, access):  # noqa: N802
        return self._Key(self.keys.setdefault(path, {}))

    def SetValueEx(self, key, name, reserved, kind, value):  # noqa: N802
        key.values[name] = value

    def DeleteKey(self, root, path):  # noqa: N802
        if path not in self.keys:
            raise FileNotFoundError(path)
        if any(other.startswith(path + "\\") for other in self.keys):
            raise OSError("ключ с подключами")
        del self.keys[path]


def test_register_adds_verb_for_docx_and_pdf():
    reg = FakeRegistry()
    exe = Path(r"C:\Apps\MD Blinder\MD Blinder.exe")
    shell.register(exe, reg)
    for ext in (".docx", ".pdf"):
        base = rf"Software\Classes\SystemFileAssociations\{ext}\shell\MDBlinder"
        assert reg.keys[base][None] == "Анонимизировать в MD Blinder"
        assert reg.keys[base]["MultiSelectModel"] == "Single"
        assert reg.keys[base + r"\command"][None] == f'"{exe}" "%1"'


def test_unregister_removes_keys_and_tolerates_missing():
    reg = FakeRegistry()
    shell.register(Path("app.exe"), reg)
    shell.unregister(reg)
    assert reg.keys == {}
    shell.unregister(reg)


def test_sync_does_nothing_outside_frozen_windows_build():
    assert not shell.available()
    assert shell.sync(True) is None


def test_file_argument_skips_switches(tmp_path):
    doc = tmp_path / "договор.docx"
    doc.write_bytes(b"x")
    assert shell.file_argument(["MD Blinder.exe", "--updated", str(doc)]) == doc
    assert shell.file_argument(["MD Blinder.exe", str(tmp_path / "нет.pdf")]) is None
    assert shell.file_argument(["MD Blinder.exe"]) is None


def test_second_launch_hands_path_to_running_instance(qtbot):
    name = f"md-blinder-test-{uuid.uuid4().hex}"
    server = InstanceServer(name)
    assert server.listen()
    with qtbot.waitSignal(server.received, timeout=3000) as signal:
        assert send_to_running(r"C:\Документы\договор.pdf", name)
    assert signal.args == [r"C:\Документы\договор.pdf"]
    with qtbot.waitSignal(server.received, timeout=3000) as signal:
        assert send_to_running(None, name)
    assert signal.args == [""]


def test_no_running_instance():
    assert not send_to_running("x.pdf", f"md-blinder-test-{uuid.uuid4().hex}", timeout_ms=100)
