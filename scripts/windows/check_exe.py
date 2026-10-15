"""Smoke-test a built application with Python absent from the child environment."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile


def check_bundle(bundle):
    bundle = Path(bundle).resolve()
    with tempfile.TemporaryDirectory(prefix='nodeharbor-exe-test-') as tmp:
        folder = Path(tmp)
        env = os.environ.copy()
        for key in ('PYTHONHOME', 'PYTHONPATH', 'NODEHARBOR_HOME', 'MIHOMO_PATH', 'GITHUB_TOKEN'):
            env.pop(key, None)
        env['LOCALAPPDATA'] = str(folder / 'profile')
        system_root = env.get('SYSTEMROOT') or env.get('SystemRoot')
        if not system_root: raise RuntimeError('Windows system directory is unavailable')
        env['PATH'] = str(Path(system_root) / 'System32')
        console = bundle / 'NodeHarbor.Console.exe'
        options = {'cwd': folder, 'env': env, 'capture_output': True, 'timeout': 30,
                   'creationflags': getattr(subprocess, 'CREATE_NO_WINDOW', 0)}
        initialized = subprocess.run([str(console), 'init'], **options)
        assert initialized.returncode == 0, 'Packaged CLI initialization failed'
        assert len(list((folder / 'profile/NodeHarbor/config').iterdir())) == 1
        assert subprocess.run([str(console), 'status'], **options).returncode == 0
        version = subprocess.run([str(console), '--version'], **options)
        assert version.returncode == 0
        report = folder / 'installation.json'
        gui = subprocess.run([str(bundle / 'NodeHarbor.exe'), '--check-installation', str(report)], **options)
        assert report.is_file(), 'Windowed EXE did not complete diagnostics'
        data = json.loads(report.read_text(encoding='utf-8'))
        assert gui.returncode == 0 and data['ok'], f'Installation check failed: {data.get("error_type")}'
        assert not any((bundle / name).exists() for name in ('config', 'data', 'logs'))
        print(json.dumps({'version': version.stdout.decode().strip(), 'ok': True,
                          'without_system_python': True, 'data_outside_program_folder': True,
                          'checks': data['checks']}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle', type=Path)
    check_bundle(parser.parse_args().bundle)
