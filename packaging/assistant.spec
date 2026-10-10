from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

root = Path(SPECPATH).parent
sys.path.insert(0, str(root / "packaging"))
sys.path.insert(0, str(root / "src"))
from build import resource_files, locked_versions

datas = [(str(path), str(path.relative_to(root).parent)) for path in resource_files(root)]
datas += collect_data_files("tg_assistant", includes=["desktop/design_tokens.json", "db/legacy_0005.json"])
for name in locked_versions(root / "packaging/windows-runtime.lock"):
    datas += copy_metadata(name)
datas += [(str(root / "build/windows/notices"), "notices")]
hidden = collect_submodules("tg_assistant") + collect_submodules("uvicorn")
hidden += collect_submodules("apscheduler.triggers") + collect_submodules("apscheduler.executors")
hidden += ["aiosqlite", "keyring.backends.Windows", "sqlalchemy.dialects.sqlite.aiosqlite", "win32crypt", "win32pipe", "win32security"]
a = Analysis(
    [str(root / "packaging/launcher.py")], pathex=[str(root / "src")],
    binaries=[], datas=datas, hiddenimports=hidden,
    excludes=["pytest", "ruff", "hatchling", "PyInstaller", "setuptools", "_distutils_hack", "cffi._shimmed_dist_utils", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="TelegramAIPersonalAssistant",
          debug=False, strip=False, upx=False, console=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="TelegramAIPersonalAssistant")
