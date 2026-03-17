# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for InventoryBot — run: pyinstaller InventoryBot.spec --clean --noconfirm
from PyInstaller.utils.hooks import collect_all, collect_data_files

block_cipher = None

authlib_datas, authlib_bins, authlib_hidden = collect_all('authlib')
anthropic_datas, anthropic_bins, anthropic_hidden = collect_all('anthropic')

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=authlib_bins + anthropic_bins,
    datas=[
        ('templates', 'templates'),
        ('static',    'static'),
        ('processor', 'processor'),
    ] + authlib_datas + anthropic_datas,
    hiddenimports=[
        'pkg_resources.py2_compat',
        'authlib.integrations.flask_client',
        'authlib.oauth2.rfc6749',
        'authlib.jose',
        'anthropic',
        'PIL._imagingtk',
        'tkinter',
        'tkinter.filedialog',
    ] + authlib_hidden + anthropic_hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='InventoryBot',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,   # keep console so users see "Running at http://localhost:5000"
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
