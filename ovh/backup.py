#!/usr/bin/env python3
"""Encrypted PostgreSQL backups to a dedicated private R2 bucket. No deletions."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone


def restricted_json(path):
    if path.stat().st_mode & 0o077:
        raise ValueError(f'{path}: group/other permissions must be disabled')
    return json.loads(path.read_text())


def sha256(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def client(config):
    import boto3
    from botocore.config import Config
    credentials = restricted_json(Path(config['credentials']))
    return boto3.client('s3', endpoint_url=config['endpoint'], region_name='auto',
                        aws_access_key_id=credentials['access_key_id'],
                        aws_secret_access_key=credentials['secret_access_key'],
                        config=Config(signature_version='s3v4',
                                      retries={'max_attempts': 4, 'mode': 'standard'},
                                      connect_timeout=10, read_timeout=120))


def upload_verified(s3, bucket, key, path, metadata):
    digest = sha256(path)
    metadata = dict(metadata, sha256=digest)
    with path.open('rb') as source:
        s3.put_object(Bucket=bucket, Key=key, Body=source,
                      ContentType='application/octet-stream', Metadata=metadata)
    head = s3.head_object(Bucket=bucket, Key=key)
    if head['ContentLength'] != path.stat().st_size or head['Metadata'] != metadata:
        raise RuntimeError('remote size/metadata mismatch')
    # A full read verifies the uploaded ciphertext rather than trusting metadata.
    response = s3.get_object(Bucket=bucket, Key=key)
    actual = hashlib.sha256()
    try:
        for chunk in response['Body'].iter_chunks(1024 * 1024):
            actual.update(chunk)
    finally:
        response['Body'].close()
    if actual.hexdigest() != digest:
        raise RuntimeError('remote ciphertext digest mismatch')
    return {'key': key, 'bytes': path.stat().st_size, 'sha256': digest}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--dump-file', type=Path,
                        help='Operator-supplied consistent dump; never marks hourly freshness')
    parser.add_argument('--class', dest='backup_class', default='hourly',
                        choices=['hourly', 'daily', 'weekly', 'migration', 'rehearsal'])
    parser.add_argument('--source-version', default='unknown')
    args = parser.parse_args()
    os.umask(0o077)
    config = restricted_json(args.config)
    if config['bucket'] != 'primeira-db-backups':
        parser.error('only the dedicated private backup bucket is allowed')
    if args.dump_file and args.backup_class not in ('migration', 'rehearsal'):
        parser.error('supplied dumps require migration or rehearsal class')
    state = Path(config['state_directory'])
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (state / 'backup.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        now = datetime.now(timezone.utc)
        stamp = now.strftime('%Y%m%dT%H%M%S.%fZ')
        with tempfile.TemporaryDirectory(prefix='work-', dir=state) as work:
            work = Path(work)
            if args.dump_file:
                dump = args.dump_file.resolve(strict=True)
                version = args.source_version
                source_kind = 'operator-dump'
            else:
                container = config['db_container']
                if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', container):
                    raise ValueError('invalid database container name')
                user = config.get('db_user', 'storehouse_user')
                database = config.get('db_name', 'storehousedb')
                for value in (user, database):
                    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', value):
                        raise ValueError('invalid database/user identifier')
                dump = work / 'database.dump'
                with dump.open('wb') as output:
                    subprocess.run(['docker', 'exec', container, 'pg_dump', '-U', user,
                                    '-d', database, '--format=custom'],
                                   check=True, stdout=output)
                version = subprocess.check_output(
                    ['docker', 'exec', container, 'pg_dump', '--version'], text=True).strip()
                source_kind = 'live-database'
            with dump.open('rb') as header:
                is_custom_dump = header.read(5) == b'PGDMP'
            if dump.stat().st_size < 5 or not is_custom_dump:
                raise ValueError('expected a PostgreSQL custom-format dump')
            encrypted = work / 'database.dump.age'
            subprocess.run(['age', '-R', config['recipients_file'], '-o', str(encrypted), str(dump)],
                           check=True)
            api_image_file = Path(config['api_image_file'])
            api_image = api_image_file.read_text().strip() if api_image_file.exists() else 'not-activated'
            manifest = {'created_at': now.isoformat(), 'source': source_kind,
                        'postgresql': version, 'api_image': api_image,
                        'dump_sha256': sha256(dump), 'dump_bytes': dump.stat().st_size,
                        'ciphertext_sha256': sha256(encrypted),
                        'ciphertext_bytes': encrypted.stat().st_size}
            manifest_file = work / 'manifest.json'
            manifest_file.write_text(json.dumps(manifest, indent=2) + '\n')
            encrypted_manifest = work / 'manifest.json.age'
            subprocess.run(['age', '-R', config['recipients_file'], '-o', str(encrypted_manifest),
                           str(manifest_file)], check=True)
            artifacts = [encrypted, encrypted_manifest]
            recovery_files = config.get('recovery_files', [])
            if recovery_files:
                archive = work / 'configuration.tar'
                with tarfile.open(archive, 'w') as output:
                    for item in recovery_files:
                        path = Path(item).resolve(strict=True)
                        output.add(path, arcname=path.relative_to('/'), recursive=False)
                sealed = work / 'configuration.tar.age'
                subprocess.run(['age', '-R', config['recipients_file'], '-o', str(sealed), str(archive)],
                               check=True)
                artifacts.append(sealed)
            # Retain ciphertext on upload failure. Never spool plaintext dumps or keys.
            spool = state / 'spool' / stamp
            spool.mkdir(mode=0o700, parents=True)
            for path in artifacts:
                shutil.copyfile(path, spool / path.name)
            s3 = client(config)
            classes = [args.backup_class]
            # UTC boundaries yield one daily and weekly snapshot without overwrites.
            if args.backup_class == 'hourly' and now.hour == 0:
                classes.append('daily')
                if now.weekday() == 6:
                    classes.append('weekly')
            uploaded = []
            for kind in classes:
                prefix = f'{kind}/{stamp}'
                for path in artifacts:
                    uploaded.append(upload_verified(s3, config['bucket'],
                                                    f'{prefix}/{path.name}', path,
                                                    {'source': source_kind, 'class': kind}))
            report = {'completed_at': datetime.now(timezone.utc).isoformat(),
                      'bucket': config['bucket'], 'source': source_kind, 'objects': uploaded}
            (state / f'{stamp}.json').write_text(json.dumps(report, indent=2) + '\n')
            if not args.dump_file and args.backup_class == 'hourly':
                pending = state / '.last-success.json'
                pending.write_text(json.dumps(report) + '\n')
                pending.replace(state / 'last-success.json')
            print(json.dumps(report))


if __name__ == '__main__':
    main()
