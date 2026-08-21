# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

block_cipher = None

# Ensure bin/fpcalc.exe is included in the package
datas = [
    ('bin/fpcalc.exe', 'bin') if os.path.exists('bin/fpcalc.exe') else ('bin', 'bin'),
]

# Hidden imports that PyInstaller might miss with PySide6/QtMultimedia
hiddenimports = [
    'tkinter',
    'mutagen',
    'mutagen.mp3',
    'mutagen.flac',
    'mutagen.oggvorbis',
    'mutagen.mp4',
    'mutagen.wave',
    'numpy',
    'scipy',
    'rich',
]

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'torch', 'torchvision', 'torchaudio',
        'tensorflow', 'tensorboard', 'keras',
        'cv2', 'PIL', 'Pillow',
        'pandas', 'scipy.spatial.transform',
        'matplotlib', 'tkinter', 'test', 'unittest',
        'PyQt5', 'PyQt6',
        'IPython', 'jupyter', 'notebook',
        'sklearn', 'scikit-learn',
        'pygame', 'yt_dlp', 'grpc', 'h5py'
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='AudioDuplicateDetector',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # No terminal window popup for standard users
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
