# PIK mobile administration

The Persian RTL web panel shares the bot's SQLite database and domain services.
It provides transaction/receipt review, accounting, subscriber search and bans,
quote history, customer card archive, deposit cards including images, product
pricing settings, bot text/settings, direct messages, and manual broadcasts.
Dashboard activity and broadcast progress refresh every 45 seconds while visible.

Production: https://65.109.182.47:9443

Each configured Telegram admin sets their own username/password using `/webpanel`
in the bot's private chat. No default account is created. Password messages are
deleted when Telegram permits it. Password hashes use scrypt; session tokens are
stored hashed, with HttpOnly/Secure/SameSite cookies, CSRF checks, admin status
rechecks and login throttling. Password reset revokes existing sessions. The bot
also offers an explicit session revocation action.

Install on Android through Chrome's install menu or on iOS through Safari's
Share → Add to Home Screen. The service worker only caches the offline notice;
authenticated responses and financial records are not cached for offline use.

## Server deployment

Install nginx and certbot >=5.4 in `/opt/pik-acme`. Install application requirements
in the existing bot environment. After reviewing/pushing changes, pull them in
`/opt/PIKExchangeBot`, run `bash deploy/prepare_web_tls.sh` once, then
`.venv/bin/python tools/deploy_web.py`. The latter takes a SQLite online backup
before initializing the web schema and restarting `pik-web` and `pik-bot`.

The web service binds only to 127.0.0.1:8789, nginx serves TLS on 9443, and port80
serves ACME challenges and redirects to 9443. Existing services on 443/8443 remain
in place. `pik-cert-renew.timer` checks every six hours and reloads nginx after
successful renewal of the Let's Encrypt IP certificate (160-hour lifetime).
Check `systemctl status pik-web pik-bot pik-cert-renew.timer` and use
`/opt/pik-acme/bin/certbot renew --dry-run` to test renewal.

References: https://letsencrypt.org/2026/03/11/shorter-certs-certbot/ and
https://letsencrypt.org/2026/01/15/6day-and-ip-general-availability/.

For a local preview, choose a disposable DB_PATH, set PIK_WEB_DEV=1 and start
`python -m webapp.server`. Development mode binds to loopback and disables the
Secure cookie flag only; it does not bypass authentication. Never expose that
development process to a public interface.
