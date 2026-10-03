#!/bin/sh
# Run on the OVH host after copying the exact deployed image and credentials.
set -eu
test "$(id -u)" = 0
base=/srv/primeira-rehearsal
install -d -m 700 "$base/oci"
for file in api-image.tar.gz source-app.env; do
  install -m 600 "/home/ubuntu/$file" "$base/$file"
  rm "/home/ubuntu/$file"
done
install -m 600 /home/ubuntu/oci_api_key.pem "$base/oci/oci_api_key.pem"
rm /home/ubuntu/oci_api_key.pem
python3 - "$base" <<'PY'
import os, pathlib, sys
base = pathlib.Path(sys.argv[1])
allowed = {'OCI_FINGERPRINT', 'OCI_USER_ID', 'OCI_TENANCY_ID',
           'GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET', 'JWT_SECRET',
           'ISBNDB_API_KEY', 'OPENAI_API_KEY', 'POSTGRES_USER', 'POSTGRES_PASSWORD'}
lines = [line for line in (base / 'source-app.env').read_text().splitlines()
         if line.partition('=')[0] in allowed]
lines += ['SPRING_PROFILES_ACTIVE=prod', 'POSTGRES_HOST=primeira-db-rehearsal',
          'OCI_PRIVATE_KEY_PATH=/run/oci/oci_api_key.pem',
          'JAVA_TOOL_OPTIONS=-XX:MaxRAMPercentage=65.0']
lines = [line for line in lines if not line.startswith('JWT_SECRET=')]
lines += ['JWT_SECRET=migration-rehearsal-only-key-not-valid-in-production']
(base / 'app.env').write_text('\n'.join(lines) + '\n')
os.chmod(base / 'app.env', 0o600)
PY
gzip -dc "$base/api-image.tar.gz" | docker load
# Preserve the source image ID so the rehearsal never builds a different revision.
docker image inspect storehouse-api:latest --format '{{.Id}}' > "$base/api-image-id"
image=$(cat "$base/api-image-id")
docker network inspect primeira-rehearsal >/dev/null 2>&1 || docker network create --internal primeira-rehearsal
docker rm primeira-db-rehearsal
docker run -d --name primeira-db-rehearsal --network primeira-rehearsal --memory 1g \
  --mount type=volume,source=primeira-pg-rehearsal,target=/var/lib/postgresql/data \
  postgres:16-bookworm
attempt=0
until docker exec primeira-db-rehearsal pg_isready -U storehouse_user -d storehousedb >/dev/null; do
  attempt=$((attempt + 1)); test "$attempt" -lt 30; sleep 2
done
docker run -d --name primeira-api-rehearsal --network primeira-rehearsal --memory 2g \
  --env-file "$base/app.env" --mount "type=bind,source=$base/oci,target=/run/oci,readonly" \
  -p 127.0.0.1:18080:8080 "$image"
echo 'Rehearsal containers started on an internal network. No production routing changed.'
