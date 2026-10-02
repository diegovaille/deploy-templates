#!/usr/bin/env python3
"""Exercise password login on the disposable DB, restoring passwords afterwards."""
import json
import secrets
import subprocess
import urllib.request

def sql(statement):
    return subprocess.check_output([
        'docker', 'exec', '-i', 'primeira-db-rehearsal', 'psql', '-U', 'storehouse_user',
        '-d', 'storehousedb', '-X', '-qAt', '-v', 'ON_ERROR_STOP=1', '-c', statement], text=True).strip()

def literal(value):
    return 'NULL' if value is None else "'" + str(value).replace("'", "''") + "'"

ip = subprocess.check_output(['docker', 'inspect', 'primeira-api-rehearsal', '--format',
    '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'], text=True).strip()
base = f'http://{ip}:8080'
def post(path, body):
    request = urllib.request.Request(base + path, data=json.dumps(body).encode(),
        headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=10) as response:
        assert response.status == 200
        return json.load(response)

extension_existed = sql("SELECT count(*) FROM pg_extension WHERE extname='pgcrypto'") == '1'
sql('CREATE EXTENSION IF NOT EXISTS pgcrypto')
try:
    for kind, condition in [('store', "o.cnpj IS DISTINCT FROM '60774613000108'"),
                            ('pinguim', "o.cnpj = '60774613000108'")]:
        record = sql("SELECT json_build_object('id', u.id, 'username', u.username, 'password', u.password) "
            "FROM usuario u JOIN organizacao_usuario ou ON ou.usuario_id=u.id "
            "JOIN organizacao o ON o.id=ou.organizacao_id "
            "WHERE u.username IS NOT NULL AND " + condition +
            " AND EXISTS (SELECT 1 FROM filial f WHERE f.organizacao_id=o.id) ORDER BY u.id LIMIT 1")
        if not record:
            raise RuntimeError(f'No existing login fixture for {kind}')
        user = json.loads(record)
        password = secrets.token_urlsafe(24)
        try:
            sql(f"UPDATE usuario SET password=crypt({literal(password)}, gen_salt('bf', 10)) WHERE id={literal(user['id'])}")
            credentials = {'username': user['username'], 'password': password}
            if kind == 'store':
                login = post('/api/auth/login', credentials)
                organizations = post('/api/auth/organizacoes', {'tempToken': login['tempToken']})
                org = organizations['organizacoes'][0]
                result = post('/api/auth/token', {'tempToken': login['tempToken'],
                    'organizacaoId': org['organizacaoId'], 'filialId': org['filiais'][0]['filialId']})
            else:
                result = post('/api/pinguim-admin/auth/login', credentials)
            assert result.get('token')
            print(f'{kind}: password login and token issuance succeeded')
        finally:
            sql(f"UPDATE usuario SET password={literal(user['password'])} WHERE id={literal(user['id'])}")
finally:
    if not extension_existed:
        sql('DROP EXTENSION pgcrypto')
print('Original passwords restored in the disposable copy; production was never modified.')
