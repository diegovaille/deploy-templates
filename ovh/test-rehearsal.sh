#!/bin/sh
# Uses a separate loopback-only Nginx instance; never reloads production.
set -eu
test "$(id -u)" = 0
base=/srv/primeira-rehearsal
install -d -m 755 /srv/primeira/sites
install -d -m 700 "$base/nginx"
install -d -m 755 "$base/static-source"
tar -xzf /home/ubuntu/sites.tar.gz -C "$base/static-source"
for site in frontend preview pinguimice pinguimice-admin; do
  install -d -m 755 "/srv/primeira/sites/$site/releases"
  target="/srv/primeira/sites/$site/releases/oracle-snapshot"
  test ! -e "$target"
  cp -a "$base/static-source/$site" "$target"
  chmod -R a+rX "$target"
  ln -s "$target" "/srv/primeira/sites/$site/current"
done
ip=$(docker inspect primeira-api-rehearsal --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}')
python3 /home/ubuntu/render-nginx.py "$base/nginx" --rehearsal --rehearsal-api-ip "$ip"
cat > "$base/nginx-test.conf" <<'EOF'
pid /run/primeira-nginx-rehearsal.pid;
error_log /var/log/nginx/primeira-rehearsal.error.log;
events { worker_connections 128; }
http {
    include /etc/nginx/mime.types;
    include /srv/primeira-rehearsal/nginx/*.conf;
}
EOF
nginx -t -c "$base/nginx-test.conf"
nginx -c "$base/nginx-test.conf"
trap 'nginx -s quit -c "$base/nginx-test.conf"' EXIT
check() {
  host=$1; path=$2; expected=$3
  code=$(curl --silent --show-error --max-time 10 -o /dev/null -w '%{http_code}' \
    -H "Host: $host" "http://127.0.0.1:18081$path")
  test "$code" = "$expected"
  printf '%s %s: %s\n' "$host" "$path" "$code"
}
for domain in primeira.app.br preview.primeira.app.br pinguimice.com.br www.pinguimice.com.br admin.pinguimice.com.br; do
  check "$domain" / 200
  check "$domain" /migration-route-check 200
  check "$domain" /missing-migration.js 404
  check "$domain" /.env 403
done
check api.primeira.app.br /api/produtos/tipos 200
check api.primeira.app.br /api/produtos 401
check admin.pinguimice.com.br /api/produtos/tipos 200
check admin.pinguimice.com.br /api/pinguim-admin/sabores 401
systemctl is-active nginx tudofirme.service
nginx -t
