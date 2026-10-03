#!/usr/bin/env python3
"""Copy a verified Oracle snapshot to R2; never changes source or database URLs."""
import argparse
import base64
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--files', type=Path, required=True)
    parser.add_argument('--credentials', type=Path, required=True)
    parser.add_argument('--account-id', required=True)
    parser.add_argument('--bucket', default='storehouse-images')
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--upload', action='store_true', help='Default is local validation only')
    args = parser.parse_args()
    objects = json.loads(args.manifest.read_text())
    assert len({o['name'] for o in objects}) == len(objects), 'duplicate keys'
    for obj in objects:
        key = PurePosixPath(obj['name'])
        assert not key.is_absolute() and '..' not in key.parts, 'unsafe key'
        body = b'' if obj['name'].endswith('/') and obj['size'] == 0 else (args.files / key).read_bytes()
        assert len(body) == obj['size'], 'snapshot size mismatch'
        assert base64.b64encode(hashlib.md5(body).digest()).decode() == obj['md5'], 'snapshot checksum mismatch'
    if not args.upload:
        print(json.dumps({'validated': len(objects), 'bytes': sum(o['size'] for o in objects)}))
        return
    assert args.credentials.stat().st_mode & 0o077 == 0, 'credentials must be mode 0600'
    credentials = json.loads(args.credentials.read_text())
    env = os.environ.copy()
    env.update(AWS_ACCESS_KEY_ID=credentials['access_key_id'], AWS_SECRET_ACCESS_KEY=credentials['secret_access_key'], AWS_DEFAULT_REGION='auto', AWS_EC2_METADATA_DISABLED='true', AWS_PAGER='')
    env.pop('AWS_SESSION_TOKEN', None)
    prefix = ['aws', '--endpoint-url', f'https://{args.account_id}.r2.cloudflarestorage.com', 's3api']
    def call(params):
        result = subprocess.run(prefix + params, env=env, capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(result.stderr.strip())
        return json.loads(result.stdout or '{}')
    with tempfile.TemporaryDirectory() as temporary:
        empty = Path(temporary) / 'empty'
        empty.write_bytes(b'')
        def copy(obj):
            body = empty if obj['name'].endswith('/') and obj['size'] == 0 else args.files / obj['name']
            headers = obj['headers']
            params = ['put-object', '--bucket', args.bucket, '--key', obj['name'], '--body', str(body), '--content-md5', obj['md5']]
            for header in ('content-type', 'cache-control', 'content-disposition', 'content-encoding', 'content-language'):
                if header in headers:
                    params += ['--' + header, headers[header]]
            metadata = {k.removeprefix('opc-meta-'): v for k, v in headers.items() if k.startswith('opc-meta-')}
            if metadata:
                params += ['--metadata', json.dumps(metadata)]
            call(params)
            remote = call(['head-object', '--bucket', args.bucket, '--key', obj['name']])
            assert remote['ContentLength'] == obj['size'], 'remote size mismatch'
            expected = base64.b64decode(obj['md5']).hex()
            assert remote['ETag'].strip('"') == expected, 'remote checksum mismatch'
            for header, field in [('content-type', 'ContentType'), ('cache-control', 'CacheControl'), ('content-disposition', 'ContentDisposition'), ('content-encoding', 'ContentEncoding'), ('content-language', 'ContentLanguage')]:
                if header in headers:
                    assert remote.get(field) == headers[header], 'remote header mismatch'
            assert remote.get('Metadata', {}) == metadata, 'remote metadata mismatch'
            return {'key': obj['name'], 'size': obj['size'], 'md5': expected, 'verified': True}
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            report = list(executor.map(copy, objects))
    args.report.write_text(json.dumps(report, indent=2))
    print(json.dumps({'copied_and_verified': len(report), 'bytes': sum(o['size'] for o in objects)}))


if __name__ == '__main__':
    main()
