#!/usr/bin/env python3
"""Read-only smoke against the internal rehearsal API; never print user data."""
import base64
import hashlib
import hmac
import json
import subprocess
import time
import urllib.request

def output(*args):
    return subprocess.check_output(args, text=True).strip()

ip = output('docker', 'inspect', 'primeira-api-rehearsal', '--format',
            '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}')
base = f'http://{ip}:8080'
for attempt in range(30):
    try:
        with urllib.request.urlopen(base + '/api/produtos/tipos', timeout=3) as response:
            assert response.status == 200
        break
    except OSError:
        if attempt == 29:
            raise
        time.sleep(2)

# The rehearsal JWT key is deliberately different from the production key.
secret = 'migration-rehearsal-only-key-not-valid-in-production'
record = output('docker', 'exec', 'primeira-db-rehearsal', 'psql', '-U', 'storehouse_user',
                '-d', 'storehousedb', '-Atc',
                "SELECT json_build_object('organizacaoId', organizacao_id, 'filialId', id) FROM filial ORDER BY id LIMIT 1")
claims = json.loads(record)
claims.update(sub='migration-rehearsal@example.invalid', perfil='ADMIN',
              iat=int(time.time()), exp=int(time.time()) + 300)
def encode(value):
    return base64.urlsafe_b64encode(value).rstrip(b'=')
body = encode(b'{"alg":"HS256","typ":"JWT"}') + b'.' + encode(json.dumps(claims).encode())
token = (body + b'.' + encode(hmac.new(secret.encode(), body, hashlib.sha256).digest())).decode()
for path in ['/api/produtos', '/api/pinguim-admin/sabores']:
    request = urllib.request.Request(base + path, headers={'Authorization': 'Bearer ' + token})
    with urllib.request.urlopen(request, timeout=10) as response:
        data = json.load(response)
        assert response.status == 200
        assert isinstance(data, (list, dict))
    print(f'{path}: 200, valid JSON')
print('Authenticated reads succeeded with a rehearsal-only token; real OAuth login was not tested.')
