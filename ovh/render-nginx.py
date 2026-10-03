#!/usr/bin/env python3
"""Render PIB virtual hosts without editing the running Nginx configuration."""
import argparse
import ipaddress
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('output', type=Path)
parser.add_argument('--rehearsal', action='store_true')
parser.add_argument('--rehearsal-api-ip', type=ipaddress.ip_address)
parser.add_argument('--sites', nargs='+', choices=['frontend', 'preview', 'pinguimice', 'pinguimice-admin', 'backend'])
parser.add_argument('--certificate-root', type=Path, default=Path('/etc/letsencrypt/live'))
parser.add_argument('--trusted-api-proxy', action='append', type=ipaddress.ip_address, default=[],
                    help='Exact legacy proxy IP allowed to supply the API client address')
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=True)
sites = {
    'frontend': ['primeira.app.br'],
    'preview': ['preview.primeira.app.br'],
    'pinguimice': ['pinguimice.com.br', 'www.pinguimice.com.br'],
    'pinguimice-admin': ['admin.pinguimice.com.br'],
    'backend': ['api.primeira.app.br'],
}
common = '''log_format primeira_json escape=json
  '{"time":"$time_iso8601","request_id":"$request_id","host":"$host",'
  '"status":$status,"bytes":$bytes_sent,"duration":$request_time}';
limit_req_zone $binary_remote_addr zone=primeira_api:10m rate=20r/s;
limit_req_zone $binary_remote_addr zone=primeira_login:10m rate=10r/m;
limit_conn_zone $binary_remote_addr zone=primeira_connections:10m;
'''
(args.output / '00-primeira.conf').write_text(common)
if args.rehearsal_api_ip and not args.rehearsal:
    parser.error('--rehearsal-api-ip requires --rehearsal')
api_host = str(args.rehearsal_api_ip) if args.rehearsal_api_ip else '127.0.0.1'
if ':' in api_host:
    api_host = f'[{api_host}]'
api_port = 8080 if args.rehearsal_api_ip or not args.rehearsal else 18080
proxy = f'''proxy_pass http://{api_host}:{api_port};
        proxy_http_version 1.1;
        proxy_set_header Host api.primeira.app.br;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $host;
        proxy_set_header Forwarded "";
        proxy_set_header Connection "";
        proxy_connect_timeout 5s;
        proxy_send_timeout 30s;
        proxy_read_timeout 60s;'''
for site, domains in sites.items():
    if args.sites and site not in args.sites:
        continue
    names = ' '.join(domains)
    cert = domains[0]
    if args.rehearsal:
        listen = 'listen 127.0.0.1:18081;'
        redirect = ''
    else:
        listen = f'''listen 443 ssl;
    ssl_certificate {args.certificate_root}/{cert}/fullchain.pem;
    ssl_certificate_key {args.certificate_root}/{cert}/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;'''
        redirect = f'''server {{
    listen 80;
    server_name {names};
    location ^~ /.well-known/acme-challenge/ {{ root /var/www/letsencrypt; }}
    location / {{ return 301 https://$host$request_uri; }}
}}
'''
    if site == 'backend':
        if args.trusted_api_proxy:
            listen += '\n    ' + '\n    '.join(
                f'set_real_ip_from {address};' for address in args.trusted_api_proxy)
            listen += '\n    real_ip_header X-Forwarded-For;\n    real_ip_recursive on;'
        locations = f'''location ~ ^/api/(auth|pinguim-admin/auth)/ {{
        limit_req zone=primeira_login burst=10 nodelay;
        {proxy}
    }}
    location / {{
        limit_req zone=primeira_api burst=40 nodelay;
        {proxy}
    }}'''
    else:
        locations = '''location ~ /\\. { deny all; }
    location ~* \\.(js|css|woff2?|png|jpe?g|svg|ico|webp)$ {
        expires 1d;
        try_files $uri =404;
    }
    location / {
        expires -1;
        try_files $uri $uri/ /index.html;
    }'''
        if site == 'pinguimice-admin':
            for path in ['/api/auth/login', '/api/pinguim-admin/auth/login']:
                locations += f'''\n    location = {path} {{
        limit_req zone=primeira_login burst=10 nodelay;
        {proxy}
    }}'''
            locations += f'''\n    location ^~ /api/ {{
        limit_req zone=primeira_api burst=40 nodelay;
        {proxy}
    }}'''
    csp = "default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net; font-src 'self' data: https://fonts.gstatic.com https://cdn.jsdelivr.net; img-src 'self' data: https:; connect-src 'self' https://api.primeira.app.br; form-action 'self' https://api.primeira.app.br"
    # Quagga uses generated workers/functions. Enforce only after barcode browser QA.
    csp_header = 'Content-Security-Policy-Report-Only' if site in ('frontend', 'preview') else 'Content-Security-Policy'
    if site in ('frontend', 'preview'):
        csp += "; worker-src 'self' blob:"
    content = redirect + f'''server {{
    {listen}
    server_name {names};
    root /srv/primeira/sites/{site}/current;
    index index.html;
    access_log /var/log/nginx/primeira-{site}.access.log primeira_json;
    error_log /var/log/nginx/primeira-{site}.error.log warn;
    client_max_body_size 15m;
    client_body_timeout 15s;
    client_header_timeout 15s;
    send_timeout 30s;
    limit_conn primeira_connections 30;
    limit_req_status 429;
    add_header X-Content-Type-Options nosniff always;
    add_header X-Frame-Options DENY always;
    add_header Referrer-Policy strict-origin-when-cross-origin always;
    add_header {csp_header} "{csp}" always;
    {locations}
}}
'''
    (args.output / f'{site}.conf').write_text(content)
