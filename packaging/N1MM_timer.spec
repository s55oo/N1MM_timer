# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for N1MM_timer – built for a low antivirus false-positive rate:
#   * one-folder build (no self-extraction of a payload into %TEMP% at every start)
#   * no UPX packing
#   * VERSIONINFO resource (company / product / version)
#   * bootloader compiled from source on the build machine (see build.bat / CI)
# Produces dist\N1MM_timer\ with:
#   N1MM_timer.exe          the window (no console)
#   N1MM_timer_console.exe  --console / --check / --db from a command prompt
import os

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
VERSION_FILE = os.path.join(SPECPATH, "version_info.txt")

a = Analysis(
    [os.path.join(ROOT, "run_timer.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[],
    hiddenimports=["tkinter.simpledialog"],
    hookspath=[],
    runtime_hooks=[],
    # not needed at runtime – smaller bundle, fewer things to scan
    excludes=["test", "unittest", "pydoc", "doctest", "pdb", "setuptools", "pip", "lib2to3"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

common = dict(
    exclude_binaries=True,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    version=VERSION_FILE,
    codesign_identity=None,
    entitlements_file=None,
)

gui = EXE(pyz, a.scripts, [], name="N1MM_timer", console=False, **common)
cli = EXE(pyz, a.scripts, [], name="N1MM_timer_console", console=True, **common)

coll = COLLECT(
    gui, cli,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="N1MM_timer",
)
