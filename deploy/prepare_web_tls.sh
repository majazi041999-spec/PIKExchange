#!/usr/bin/env bash
set -euo pipefail
PIK_DEPLOY_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
install -d -m 755 /var/www/pik-acme/.well-known/acme-challenge
install -m 644 "$PIK_DEPLOY_DIR/pik-nginx-http.conf" /etc/nginx/conf.d/pik-http.conf
nginx -t
systemctl reload nginx
ufw allow 80/tcp
ufw allow 9443/tcp
/opt/pik-acme/bin/certbot certonly --non-interactive --agree-tos --register-unsafely-without-email --preferred-profile shortlived --webroot --webroot-path /var/www/pik-acme --ip-address 65.109.182.47
install -m 644 "$PIK_DEPLOY_DIR/pik-nginx-tls.conf" /etc/nginx/conf.d/pik-tls.conf
nginx -t
systemctl reload nginx
install -m 644 "$PIK_DEPLOY_DIR/pik-cert-renew.service" /etc/systemd/system/pik-cert-renew.service
install -m 644 "$PIK_DEPLOY_DIR/pik-cert-renew.timer" /etc/systemd/system/pik-cert-renew.timer
systemctl daemon-reload
systemctl enable --now pik-cert-renew.timer
