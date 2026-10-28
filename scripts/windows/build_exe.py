"""Build a Windows bundle only from an audited source copy; never bundle user data."""
from __future__ import annotations

import hashlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import release


def make_icon(path):
    from PIL import Image, ImageDraw
    image = Image.new('RGBA', (256, 256))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((4, 4, 252, 252), radius=52, fill='#275DDE')
    draw.line([(128, 49), (207, 128), (128, 207), (49, 128), (128, 49)], fill='white', width=13, joint='curve')
    draw.line([(128, 49), (128, 207)], fill='#BFCFFF', width=8)
    for x, y in ((128, 49), (207, 128), (128, 207), (49, 128), (128, 128)):
        draw.ellipse((x-14, y-14, x+14, y+14), fill='white')
    image.save(path, format='ICO', sizes=[(n, n) for n in (16, 24, 32, 48, 64, 128, 256)])


def copy_licenses(bundle, source):
    folder = bundle / 'licenses'; folder.mkdir()
    for name in ('requests', 'PyYAML', 'urllib3', 'idna', 'charset-normalizer', 'certifi', 'pyinstaller'):
        distribution = metadata.distribution(name)
        files = [p for p in distribution.files or []
                 if p.name.lower().startswith(('license', 'copying')) and p.suffix not in {'.py', '.pyc'}]
        if not files:
            raise RuntimeError(f'Missing license for {name}')
        for index, entry in enumerate(files):
            shutil.copyfile(distribution.locate_file(entry), folder / f'{name}-{index}-{entry.name}')
    python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    if not python_license.is_file():
        raise RuntimeError('Python license is required for the Windows runtime bundle')
    shutil.copyfile(python_license, folder / 'Python.txt')
    for name in ('Tcl', 'Tk', 'OpenSSL'):
        shutil.copyfile(source / 'docs/licenses' / f'{name}.txt', folder / f'{name}.txt')
    for name in ('LICENSE', 'README.md', 'THIRD_PARTY_NOTICES.md', 'SECURITY.md'):
        shutil.copyfile(source / name, bundle / name)
    shutil.copytree(source / 'docs', bundle / 'docs')


def audit_bundle(bundle, private_root):
    from PyInstaller.archive.readers import CArchiveReader
    values = release.private_values(private_root)
    identities = {os.environ.get('USERNAME', ''), str(Path.home()), str(private_root), sys.base_prefix}
    identities = {s.encode().lower() for s in identities if len(s) >= 5}

    def scan(name, data):
        release.scan(name, data, values)
        folded = data.lower().replace(b'\\', b'/')
        if any(value.replace(b'\\', b'/') in folded for value in identities):
            raise release.AuditError(f'{name}: local build identity/path detected')

    modules = 0
    for path in bundle.rglob('*'):
        if not path.is_file(): continue
        name = path.relative_to(bundle).as_posix()
        if name.startswith(('config/', 'data/', 'logs/', 'backups/')) or path.name.lower() == 'mihomo.exe':
            raise release.AuditError(f'{name}: private runtime file is not publishable')
        if path.suffix == '.exe' and path.name not in {'NodeHarbor.exe', 'NodeHarbor.Console.exe'}:
            raise release.AuditError(f'{name}: unexpected executable')
        scan(name, path.read_bytes())
        if path.suffix == '.exe':
            archive = CArchiveReader(str(path))
            for entry, info in archive.toc.items():
                if info[-1] == 'z':
                    pyz = archive.open_embedded_archive(entry)
                    for module in pyz.toc:
                        raw = pyz.extract(module, raw=True)
                        if raw is not None: scan(f'{path.name}:{module}', raw); modules += 1
                else:
                    scan(f'{path.name}:{entry}', archive.extract(entry))
        elif path.suffix == '.zip':
            with zipfile.ZipFile(path) as archive:
                for entry in archive.infolist():
                    if not entry.is_dir(): scan(f'{name}:{entry.filename}', archive.read(entry))
    return modules


def build():
    if os.name != 'nt' or struct.calcsize('P') != 8:
        raise RuntimeError('Build on 64-bit Windows using 64-bit CPython')
    metadata.version('pyinstaller')
    source, count = release.prepare(ROOT)
    project = source / 'src/nodeharbor/__init__.py'
    import ast
    version = next(ast.literal_eval(n.value) for n in ast.parse(project.read_text(encoding='utf-8')).body
                   if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == '__version__' for t in n.targets))
    output = ROOT / '.release' / f'windows-{version}-{uuid.uuid4().hex[:8]}'
    work = output / 'work'; work.mkdir(parents=True)
    icon = work / 'NodeHarbor.ico'; make_icon(icon)
    from packaging.version import Version
    parsed_version = Version(version)
    numbers = (parsed_version.major, parsed_version.minor, parsed_version.micro,
               parsed_version.pre[1] if parsed_version.pre else 0)
    version_flags = 0x2 if parsed_version.is_prerelease else 0
    version_file = work / 'version.txt'
    version_file.write_text(f'''VSVersionInfo(
        ffi=FixedFileInfo(filevers={numbers!r}, prodvers={numbers!r}, mask=0x3f, flags={version_flags},
                         OS=0x40004, fileType=0x1, subtype=0, date=(0,0)),
        kids=[StringFileInfo([StringTable('040904B0', [
            StringStruct('CompanyName', 'NodeHarbor contributors'),
            StringStruct('FileDescription', 'NodeHarbor desktop and CLI'),
            StringStruct('FileVersion', {version!r}),
            StringStruct('ProductName', 'NodeHarbor'), StringStruct('ProductVersion', {version!r}),
            StringStruct('LegalCopyright', 'GPL-3.0-only; see LICENSE')])]),
            VarFileInfo([VarStruct('Translation', [1033, 1200])])])''', encoding='utf-8')
    spec = work / 'NodeHarbor.spec'
    spec.write_text(f'''
from pathlib import Path
root = Path({str(source)!r})
a = Analysis([str(root/'scripts/windows/frozen_gui.py'), str(root/'scripts/windows/frozen_console.py')],
             pathex=[str(root/'src')], binaries=[], datas=[], hiddenimports=[],
             excludes=['pytest', 'pip', 'PIL'], noarchive=False, optimize=1)
pyz = PYZ(a.pure)
gui_scripts = [s for s in a.scripts if s[0] != 'frozen_console']
console_scripts = [s for s in a.scripts if s[0] != 'frozen_gui']
gui = EXE(pyz, gui_scripts, [], exclude_binaries=True, name='NodeHarbor',
          console=False, icon={str(icon)!r}, version={str(version_file)!r}, upx=False)
console = EXE(pyz, console_scripts, [], exclude_binaries=True, name='NodeHarbor.Console',
              console=True, icon={str(icon)!r}, version={str(version_file)!r}, upx=False)
COLLECT(gui, console, a.binaries, a.datas, name='NodeHarbor', upx=False)
''', encoding='utf-8')
    with (output / 'build.log').open('w', encoding='utf-8') as log:
        result = subprocess.run([sys.executable, '-m', 'PyInstaller', '--distpath', str(output / 'app'),
                                 '--workpath', str(work / 'cache'), str(spec)], cwd=source,
                                stdout=log, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError(f'PyInstaller build failed; inspect {output / "build.log"}')
    bundle = output / 'app/NodeHarbor'
    copy_licenses(bundle, source)
    modules = audit_bundle(bundle, ROOT)
    archive = output / f'NodeHarbor-{version}-windows-x64.zip'
    with zipfile.ZipFile(archive, 'x', zipfile.ZIP_DEFLATED, compresslevel=9) as package:
        for path in sorted(bundle.rglob('*')):
            if path.is_file(): package.write(path, 'NodeHarbor/' + path.relative_to(bundle).as_posix())
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (output / 'SHA256SUMS.txt').write_text(f'{digest}  {archive.name}\n', encoding='ascii')
    report = {'version': version, 'source': str(source), 'bundle': str(bundle), 'archive': str(archive),
              'sha256': digest, 'source_files': count, 'decoded_modules_checked': modules}
    (ROOT / '.release/latest-windows.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))
    return report


if __name__ == '__main__':
    build()
