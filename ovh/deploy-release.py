#!/usr/bin/env python3
"""Deploy a PIB release; run as root on the OVH host after provisioning."""
import argparse
import fcntl
import os
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile

def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('kind', choices=['static', 'api'])
parser.add_argument('site', choices=['frontend', 'preview', 'pinguimice', 'pinguimice-admin', 'backend'])
parser.add_argument('release')
parser.add_argument('artifact', type=Path)
args = parser.parse_args()
if os.geteuid() != 0 or not re.fullmatch(r'[a-f0-9]{40}', args.release):
    parser.error('run as root and provide the full commit SHA')
if (args.kind == 'api') != (args.site == 'backend'):
    parser.error('backend is the only API site')
artifact = args.artifact.resolve()
if not artifact.is_file():
    parser.error('artifact does not exist')
base = Path('/srv/primeira')
base.mkdir(mode=0o750, exist_ok=True)
lock = (base / 'deploy.lock').open('a')
fcntl.flock(lock, fcntl.LOCK_EX)
if args.kind == 'static':
    site = base / 'sites' / args.site
    releases = site / 'releases'
    releases.mkdir(parents=True, exist_ok=True)
    current = site / 'current'
    if current.exists() and not current.is_symlink():
        parser.error('current must be a symlink; preserve any legacy files manually')
    previous = current.resolve() if current.exists() else None
    destination = releases / args.release
    staging = Path(tempfile.mkdtemp(prefix='.incoming-', dir=releases))
    try:
        with tarfile.open(artifact) as archive:
            for member in archive.getmembers():
                path = Path(member.name)
                if path.is_absolute() or '..' in path.parts or not (member.isfile() or member.isdir()):
                    raise ValueError('unsafe archive entry')
            archive.extractall(staging, filter='data')
        if not (staging / 'index.html').is_file():
            raise ValueError('archive must contain index.html at its root')
        for path in staging.rglob('*'):
            path.chmod(0o755 if path.is_dir() else 0o644)
        staging.chmod(0o755)
        if destination.exists():
            # A retry of the same commit must not silently overwrite its release.
            run('diff', '-qr', str(staging), str(destination), stdout=subprocess.DEVNULL)
        else:
            staging.rename(destination)
        pending = site / '.current-next'
        pending.unlink(missing_ok=True)
        pending.symlink_to(destination)
        pending.replace(current)
        domain = {'frontend': 'primeira.app.br', 'preview': 'preview.primeira.app.br',
                  'pinguimice': 'pinguimice.com.br', 'pinguimice-admin': 'admin.pinguimice.com.br'}[args.site]
        try:
            run('nginx', '-t')
            run('curl', '--fail', '--silent', '--show-error', '--max-time', '15',
                '--resolve', f'{domain}:443:127.0.0.1', f'https://{domain}/', stdout=subprocess.DEVNULL)
        except Exception:
            if previous:
                pending.symlink_to(previous)
                pending.replace(current)
            else:
                current.unlink()
            raise
    finally:
        if staging.exists():
            import shutil
            shutil.rmtree(staging)
else:
    directory = base / 'app'
    compose = directory / 'compose.yml'
    image_file = directory / 'api-image.env'
    if not compose.is_file() or not image_file.is_file():
        parser.error('provision compose.yml and api-image.env before deploying')
    previous = image_file.read_bytes()
    with artifact.open('rb') as source:
        unzip = subprocess.Popen(['gzip', '-dc'], stdin=source, stdout=subprocess.PIPE)
        try:
            run('docker', 'load', stdin=unzip.stdout)
        finally:
            unzip.stdout.close()
        if unzip.wait() != 0:
            raise RuntimeError('invalid compressed image')
    image_file.write_text(f'API_IMAGE=storehouse-api:{args.release}\n')
    image_file.chmod(0o600)
    command = ['docker', 'compose', '--project-directory', str(directory),
               '--env-file', str(directory / '.env'), '--env-file', str(image_file), '-f', str(compose)]
    try:
        run(*command, 'up', '-d', '--no-deps', 'api')
        # A database-backed public endpoint; a JVM startup alone is insufficient.
        run('curl', '--fail', '--silent', '--show-error', '--retry', '30', '--retry-all-errors',
            '--retry-delay', '2', '--retry-max-time', '120', '--max-time', '5',
            'http://127.0.0.1:8080/api/produtos/tipos', stdout=subprocess.DEVNULL)
    except Exception:
        image_file.write_bytes(previous)
        run(*command, 'up', '-d', '--no-deps', 'api')
        raise
print(f'Deployed {args.site} at {args.release}')
