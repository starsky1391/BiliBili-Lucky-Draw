from pathlib import Path
from PyInstaller.utils.hooks import collect_all

root = Path(SPECPATH)
datas = [(str(root / 'admin_web' / 'static'), 'admin_web/static'),
         (str(root / 'schema.sql'), '.'), (str(root / 'LICENSE'), '.')]
binaries = []
hiddenimports = ['uvicorn.logging', 'uvicorn.loops.auto', 'uvicorn.protocols.http.h11_impl',
                 'uvicorn.protocols.websockets.auto', 'uvicorn.lifespan.on']
for package in ('webview', 'selenium'):
    package_data, package_binaries, package_imports = collect_all(package)
    datas += package_data
    binaries += package_binaries
    hiddenimports += package_imports

a = Analysis([str(root / 'desktop.py')], pathex=[str(root)], binaries=binaries,
             datas=datas, hiddenimports=hiddenimports,
             excludes=['PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'gi'])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='BiliBiliLuckyDraw',
          debug=False, strip=False, upx=False, console=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='BiliBiliLuckyDraw')
