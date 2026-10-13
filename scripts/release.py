"""Prepare an allowlisted source tree and audit source/package contents locally."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import uuid
import zipfile

import yaml

ROOT_FILES = {
    '.gitignore', '.gitattributes', 'LICENSE', 'README.md', 'THIRD_PARTY_NOTICES.md',
    'CONTRIBUTING.md', 'SECURITY.md', 'pyproject.toml', 'MANIFEST.in',
    'NodeHarbor.py', 'NodeHarbor.pyw', 'Start.cmd',
}
PATTERNS = {
    'private-key': re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----'),
    'github-token': re.compile(rb'(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})'),
    'aws-key': re.compile(rb'AKIA[A-Z0-9]{16}'),
}
SENSITIVE_KEYS = {'token', 'password', 'uuid', 'server', 'host', 'gist_id_free',
                  'gist_id_self', 'sources', 'proxy', 'github_mirror', 'mihomo_path',
                  'private-key', 'private_key', 'key', 'ssh_key'}
SENSITIVE_KEYS.add('core_paths')
SENSITIVE_KEYS.add('directory')
# This exact public file was already present in legacy local source selections.
# Exempt only the subscription list entry, never a token/password or similar key.
PUBLIC_SUBSCRIPTION_BASELINES = {'https://raw.githubusercontent.com/Ruk1ng001/freeSub/main/clash.yaml'}


class AuditError(ValueError):
    pass


def allowed(name):
    p = PurePosixPath(name)
    if p.is_absolute() or any(part in {'.', '..', '__pycache__'} for part in p.parts):
        return False
    if name in ROOT_FILES:
        return True
    parts = p.parts
    if len(parts) < 2:
        return False
    if parts[0] == 'config':
        return len(parts) == 2 and p.name.endswith(('.example.yaml', '.example.json'))
    if parts[0] == 'runtime':
        return name == 'runtime/README.md'
    if parts[:2] == ('src', 'nodeharbor') or parts[0] == 'tests':
        return p.suffix == '.py'
    if parts[0] == 'scripts':
        return p.suffix in {'.py', '.sh', '.cmd', '.ps1'}
    if parts[0] == 'docs':
        return (p.suffix == '.md' or (parts[:2] == ('docs', 'images') and p.suffix == '.png')
                or (parts[:2] == ('docs', 'licenses') and p.suffix == '.txt'))
    if parts[0] == '.github':
        return p.suffix in {'.md', '.yml', '.yaml'}
    return False


def public_files(root):
    # Walk only approved trees; never traverse private runtime/data/backup directories.
    candidates = [root / name for name in ROOT_FILES]
    for directory in ('src/nodeharbor', 'tests', 'scripts', 'docs', '.github', 'config'):
        base = root / directory
        if base.is_symlink():
            raise AuditError(f'{directory}: symbolic directory is not publishable')
        if base.exists():
            candidates.extend(base.rglob('*'))
    candidates.append(root / 'runtime/README.md')
    result = []
    for path in candidates:
        name = path.relative_to(root).as_posix()
        if not allowed(name):
            continue
        if path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent != root):
            raise AuditError(f'{name}: symbolic path is not publishable')
        if path.is_file():
            result.append(path)
    return sorted(set(result))


def private_values(root):
    values = set()

    def collect(value, sensitive=False, public_subscription=False):
        if isinstance(value, dict):
            for key, item in value.items():
                collect(item, sensitive or str(key).lower() in SENSITIVE_KEYS,
                        not sensitive and str(key).lower() == 'sources')
        elif isinstance(value, list):
            for item in value:
                collect(item, sensitive, public_subscription)
        elif sensitive and isinstance(value, str) and len(value.strip()) >= 8:
            if public_subscription and value in PUBLIC_SUBSCRIPTION_BASELINES: return
            if value.replace('\\', '/') not in {'runtime/mihomo.exe', 'runtime/mihomo'}:
                values.add(value.encode('utf-8'))

    for name in ('settings.yaml', 'nodes.yaml', 'client.yaml', 'secrets.json', 'servers.json'):
        path = root / 'config' / name
        if path.exists():
            try:
                data = yaml.safe_load(path.read_text(encoding='utf-8-sig'))
            except (OSError, ValueError, yaml.YAMLError):
                raise AuditError(f'config/{name}: cannot read local privacy baseline') from None
            collect(data)
    return values


def scan(name, data, values):
    for label, pattern in PATTERNS.items():
        if pattern.search(data):
            raise AuditError(f'{name}: {label} pattern detected')
    if any(value in data for value in values):
        raise AuditError(f'{name}: known local private value detected')


def audit(root):
    files = public_files(root)
    values = private_values(root)
    for path in files:
        scan(path.relative_to(root).as_posix(), path.read_bytes(), values)
    if (root / '.git').exists():
        result = subprocess.run(['git', '-C', str(root), 'ls-files', '-z'],
                                capture_output=True, check=True)
        for raw in result.stdout.split(b'\0'):
            if raw and not allowed(raw.decode('utf-8')):
                raise AuditError('Git index contains a non-publishable path; inspect git ls-files locally')
        # Also inspect the staged bytes, which can differ from the working copy.
        for raw in result.stdout.split(b'\0'):
            if raw:
                name = raw.decode('utf-8')
                staged = subprocess.run(['git', '-C', str(root), 'show', ':' + name],
                                        capture_output=True, check=True).stdout
                scan(name, staged, values)
    return files


def prepare(root):
    files = audit(root)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    target = root / '.release' / f'nodeharbor-{stamp}-{uuid.uuid4().hex[:8]}'
    target.mkdir(parents=True, exist_ok=False)
    for source in files:
        dest = target / source.relative_to(root)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
    # Recheck the copied bytes against the original local private values.
    values = private_values(root)
    for path in public_files(target):
        scan(path.relative_to(target).as_posix(), path.read_bytes(), values)
    return target, len(files)


def check_artifacts(root, directory):
    values = private_values(root)
    artifacts = sorted(directory.glob('*.whl')) + sorted(directory.glob('*.tar.gz'))
    if not artifacts:
        raise AuditError('No wheel or source archive found')

    def check(name, data, wheel):
        parts = PurePosixPath(name).parts
        if not parts or name.startswith('/') or '..' in parts or '\\' in name:
            raise AuditError('Archive contains an unsafe path')
        path = name if wheel else '/'.join(parts[1:])
        p = PurePosixPath(path)
        metadata = (any(part.endswith(('.dist-info', '.egg-info')) for part in p.parts)
                    and p.suffix in {'.txt', '.md', ''})
        valid = allowed(path) or metadata or path in {'PKG-INFO', 'setup.cfg'}
        if wheel and path.startswith('nodeharbor/') and p.suffix == '.py':
            valid = True
        if not valid:
            raise AuditError(f'{path}: unexpected package member')
        scan(path, data, values)

    for artifact in artifacts:
        if artifact.suffix == '.whl':
            with zipfile.ZipFile(artifact) as archive:
                for item in archive.infolist():
                    if not item.is_dir():
                        check(item.filename, archive.read(item), True)
        else:
            with tarfile.open(artifact, 'r:gz') as archive:
                for item in archive.getmembers():
                    if item.isdir():
                        continue
                    if not item.isfile():
                        raise AuditError('Source archive contains a non-regular file')
                    check(item.name, archive.extractfile(item).read(), False)
    return len(artifacts)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--check', action='store_true')
    group.add_argument('--prepare', action='store_true')
    group.add_argument('--check-artifacts', type=Path)
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    try:
        if args.check:
            print(f'PASS: {len(audit(root))} public source files checked')
        elif args.prepare:
            target, count = prepare(root)
            print(f'Prepared {count} public files: {target}')
        else:
            print(f'PASS: {check_artifacts(root, args.check_artifacts)} package archives checked')
    except (AuditError, OSError, subprocess.SubprocessError) as exc:
        # AuditError messages never include a matched value; other exceptions may.
        print(f'FAIL: {exc}' if isinstance(exc, AuditError) else 'FAIL: release check could not complete')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
