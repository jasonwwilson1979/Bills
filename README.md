# BillFlow — Django website with Google sign-in

A Django website version of the BillFlow bill tracker — the same dark-themed
UI, but bills/accounts are now saved **per signed-in user on the server** and
you log in with your **Google account**.

## Quick start

```powershell
# 1) Install dependencies (already done if you followed the setup)
python -m pip install -r requirements.txt

# 2) Create / apply the database (SQLite, created on first run)
python manage.py migrate

# 3) Add your Google OAuth credentials (see below), then start the server
$env:GOOGLE_CLIENT_ID = "your-client-id.apps.googleusercontent.com"
$env:GOOGLE_CLIENT_SECRET = "your-secret"
python manage.py runserver
```

Open <http://localhost:8000> — you'll be asked to sign in with Google.

## Setting up Google sign-in (one time, ~5 minutes)

1. Go to <https://console.cloud.google.com> and create (or pick) a project.
2. **APIs & Services → OAuth consent screen**:
   - User type: *External*, fill in the app name ("BillFlow") and your email.
   - Under *Audience / Test users*, add the Google account(s) you'll sign in
     with (required while the consent screen is in "Testing" mode).
3. **APIs & Services → Credentials → Create credentials → OAuth client ID**:
   - Application type: *Web application*
   - Authorized redirect URI (add exactly this):
     `http://localhost:8000/accounts/google/login/callback/`
4. Copy the **Client ID** and **Client secret** into the environment variables
   shown in step 3 of Quick start (or set them permanently with
   `[Environment]::SetEnvironmentVariable(...)`).

Until the variables are set, the sign-in page shows a friendly warning instead
of the Google button — nothing else breaks.

## How it works

| Piece | What it does |
|---|---|
| `tracker/models.py` | `UserData` — one JSON document per user holding bills, accounts, app name and budgets |
| `tracker/views.py` | `home` (serves the SPA, injects the saved state), `api_state` (`GET/PUT /api/state/`), `login_view` (styled sign-in page) |
| `templates/tracker/app.html` | The original SPA — unmodified UX, but persistence now goes through the API instead of `localStorage` |
| `templates/account/login.html` | Styled "Continue with Google" sign-in page (django-allauth handles the OAuth flow) |
| `billflow/settings.py` | django-allauth Google provider config, read from environment variables |

Notes:

- Saving is **debounced** (~400 ms after your last change) and sends the whole
  state document with your CSRF token — simple and robust for this app's size.
- Each user's data is private to their account (`@login_required` everywhere,
  one `UserData` row per user).
- The color-theme picker is still stored per-browser (it's cosmetic).
- CSV **import and export work exactly as before** — import is fully
  client-side (parsing + column mapping + preview), the result is saved to your
  account like any other change.
- First sign-in seeds the same sample bills the original app used, so you can
  explore immediately; "Clear Data" in Settings empties your account.
- **Backup & Restore** in Settings downloads your entire page (bills, accounts,
  budgets, paycheck plans, custom categories and the app name) as a
  `billflow_backup_YYYY-MM-DD.json` file, and can restore it later — handy after
  clearing data, moving devices, or starting a fresh account.
- **Chat** (sidebar → System) gives every page a **team chat** shared with
  everyone who has access, plus **1-on-1 direct chats** between any two of
  those people (New chat → pick someone; DMs stay between the two of you).
  Conversation tabs show unread counts (tracked server-side), the sidebar
  badge totals them, and messages refresh every few seconds while the Chat
  page is open. Editors and the owner can post and delete their own messages
  (the owner moderates the team chat); viewers are read-only. The API lives
  at `/api/chat/` (`GET` list/messages, `POST` send/start-DM, `DELETE`).
- **Recurring bills show "Scheduled" in future months.** In All Bills, use the
  month navigator to see upcoming months: a recurring bill you already paid
  this month reads *Scheduled* there (with its next occurrence date), gets its
  own blue status badge and filter tab, and automatically appears in the
  Paycheck Plan when it falls inside a paycheck's coverage window. A month
  counts as paid only when a payment was actually recorded during it.

## Deploying to an Ubuntu server

Run these **from this Windows machine** — the helper uses the built-in
`ssh` / `scp` / `tar` tools, so no extra software is needed here.

### Option A — standalone (Python venv + gunicorn + nginx)

```powershell
.\deploy\deploy-to-ubuntu.ps1 -Server root@YOUR_SERVER_IP -Mode standalone -Domain bills.example.com
```

Installs everything on the server (Python venv, gunicorn as a systemd service,
nginx reverse proxy) and keeps secrets in `/opt/billflow/.env` and the SQLite
database in `/opt/billflow/data/` — both survive re-deploys. Re-run the same
command any time to ship an update. For free HTTPS add `--with-certbot` on the
server: `sudo bash deploy/ubuntu-server-setup.sh --with-certbot --domain bills.example.com`.

### Option B — Docker

```powershell
.\deploy\deploy-to-ubuntu.ps1 -Server root@YOUR_SERVER_IP -Mode docker
```

Installs Docker (if missing) and runs the app via `docker compose` with a
persistent volume for the database. For automatic HTTPS, set
`BILLFLOW_DOMAIN=yourdomain` in `/opt/billflow/.env` on the server and run
`sudo docker compose --profile caddy up -d` (Caddy obtains/renews certificates
automatically).

### Option C — Portainer (Ubuntu, pull from GitHub, pick your port)

Once the project is on GitHub (`git init`, commit, push — it is not a git
repo yet), Portainer can deploy it two ways. Both let you **choose the host
port**, so BillFlow can live alongside your other web apps.

**Variables to set in the stack's *Environment variables* section:**

| Variable | Example | Purpose |
|---|---|---|
| `BILLFLOW_PORT` | `80` | host port the app is published on (default `8000`) |
| `BILLFLOW_CONTAINER_PORT` | `581` | port gunicorn listens on *inside* the container (default `8000`) |
| `GOOGLE_CLIENT_ID` | `...apps.googleusercontent.com` | Google sign-in |
| `GOOGLE_CLIENT_SECRET` | `...` | Google sign-in |
| `BILLFLOW_ADMIN_EMAIL` | `you@gmail.com` | optional: gives that Google account access to `/admin/` (see below) |
| `DJANGO_ALLOWED_HOSTS` | `bills.example.com` | only needed once served via a domain |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | `https://bills.example.com` | needed behind a proxy |

Do **not** set `DJANGO_SECRET_KEY` — on first start the app generates a strong
key and persists it in the data volume. Avoid `$` in any variable value
(see Troubleshooting below).

**C1. Build on the server straight from your GitHub repo (no registry)**

1. Portainer → **Stacks → Add stack → Repository**:
   - Repository URL + branch `main` (private repo? store a GitHub token in
     Portainer → Settings → Git credentials first)
   - Compose path: `docker-compose.yml`
2. Add the variables from the table above (at minimum `BILLFLOW_PORT`,
   `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`).
3. **Deploy the stack** — Portainer clones the repo and builds the image on
   the server; the SQLite database lives in the `billflow-data` volume and
   the app answers on your chosen port.
   Later updates: stack → *Recreate* → *Re-pull image and redeploy*.

**C2. Pull a pre-built image from GHCR (nothing builds on the server)**

1. Push to GitHub — `.github/workflows/docker-publish.yml` publishes
   `ghcr.io/<your-user>/<your-repo>:latest` on every push to `main`
   (image appears under the repo's *Packages*).
2. Private package? Make it public (Packages → package settings → Visibility)
   or `docker login ghcr.io` on the server with a GitHub PAT.
3. Portainer → **Stacks → Add stack → Web editor** — paste
   `deploy/docker-compose.prebuilt.yml`, replace `OWNER/REPO` in the image
   line (lowercase), add the variables, deploy.
   Updates: *Re-pull image and redeploy*.

**Picking the port + making Google sign-in work**

- Direct access (`http://your-server-ip:8090`) is fine for trying it out,
  but **Google blocks plain-HTTP / raw-IP redirect URIs**, so Google sign-in
  will not work that way — you need HTTPS on a domain.
- Since your other web apps already use port 80, the easiest route is to
  point a subdomain at BillFlow's port. On your existing web server add:

  ```nginx
  server {
      listen 80;
      server_name bills.example.com;
      location / {
          proxy_pass http://127.0.0.1:8090;
          proxy_set_header Host $host;
          proxy_set_header X-Forwarded-Proto $scheme;
          proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
      }
  }
  ```

  then `sudo certbot --nginx -d bills.example.com` for the certificate.
- Finish by setting `DJANGO_ALLOWED_HOSTS=bills.example.com` +
  `DJANGO_CSRF_TRUSTED_ORIGINS=https://bills.example.com` in the stack, and
  registering `https://bills.example.com/accounts/google/login/callback/`
  in Google Cloud Console.
- No domain yet? A free dynamic-DNS name (e.g. DuckDNS) works fine with
  certbot.

### Either way, finish with Google OAuth

1. Edit `/opt/billflow/.env` on the server and fill in `GOOGLE_CLIENT_ID` /
   `GOOGLE_CLIENT_SECRET` (auto-generated file, created on first deploy).
2. Restart: standalone → `sudo systemctl restart billflow`;
   docker → `sudo docker compose up -d`.
3. Register the redirect URI in Google Cloud Console:
   `https://your-domain/accounts/google/login/callback/`

Prefer doing it manually? Copy the project to the server (git clone / scp),
then run `sudo bash deploy/ubuntu-server-setup.sh --domain your-domain` or
`sudo docker compose up -d --build`. Add `-DryRun` to the PowerShell helper to
preview every command it would run.

## Sharing your page with others

Open **Settings → Sharing** to give other Google accounts access to your page:

- **Invite someone** — enter their Gmail address and pick a role. If that
  person has already signed in to this BillFlow at least once, access is
  granted immediately; otherwise the invite stays *pending* and activates
  automatically the next time they sign in with that address.
- **Editor** can change everything on your page (bills, accounts, settings).
- **Viewer** gets read-only access — the app shows a view-only notice and
  saving is blocked server-side too.
- **They land on your page automatically** — when someone with access signs
  in they see your page first, with a banner to switch back to their own.
  They can switch anytime via that banner or the **Pages** list at the bottom
  of the sidebar. Their own page (with sample data) is separate — bills are
  per page, not per person.
- **The email must match** — access is tied to the Google account whose
  address you invited. If they sign in with a different account, they'll only
  see their own page; remove the old invite in Settings → Sharing and invite
  the correct address.
- **Rich bill notes** — the Notes field (bottom of the Add/Edit Bill form)
  supports bold/italic/underline, clickable links, colored text and an HTML
  source editor. Notes are sanitized (scripts/handlers stripped) and open in
  an expandable panel under each bill row wherever bills are listed.
- **Tag bills in chat** — hit the 💳 button in the chat composer (or type
  `[[bill:ID]]`) to embed a bill chip; clicking it jumps to the bill in
  All Bills, opens its month and highlights the row.
- **Bill titles** — every bill has an optional **Title** (what the app shows
  everywhere) separate from its **Name** (the raw bank line kept for
  reference). Import a statement line like
  `75182 PURCHASE-SIG VIRGINIA FEDERAL CR 800-285-6609 VA 00000`, tag it once
  as "Car Payment", and every future import with the same description
  auto-links to that bill as a payment and displays "Car Payment" — no more
  renaming or manual linking. Auto-matching now works whether or not the
  bill is marked recurring. CSV export includes a Title column.
- **Fully responsive on phones** — bill rows reflow to two lines, inputs use
  16px text so iOS Safari doesn't zoom on focus, modals become near-full-width
  sheets, summaries and tab rows wrap, and wide tables scroll instead of
  crushing. The desktop layout is untouched.
- **Remove** takes their access away at any time. Pending invites are listed
  alongside active members and can be removed the same way.

People with access see a **Pages** switcher in the sidebar (plus a banner
while viewing someone else's page) and can jump between their own page and
the shared one. Everyone always signs in with their own Google account; a
removed member automatically falls back to their own page.

## Django admin with your Google account

Django's admin lives at `/admin/`, but Google-created accounts are regular
users and can't open it by default. To give your account admin access, set
the stack environment variable `BILLFLOW_ADMIN_EMAIL` to your Gmail address
and recreate the stack — it is applied on every container start *and* on
every sign-in:

```text
BILLFLOW_ADMIN_EMAIL=you@gmail.com
```

Then sign in with Google as usual and open `https://your-domain/admin/` —
no password needed, your existing session is enough. There you can inspect
users, sharing memberships and each page's stored JSON document.

## Fixing admin access ("You are authenticated as ..., but are not authorized")

The `/admin/` section needs the **staff flag**, which BillFlow grants
automatically to the account whose **Google email matches the
`BILLFLOW_ADMIN_EMAIL` stack variable** (checked at every container start and
on every sign-in). If you see Django's *"authenticated as ..., but are not
authorized"* message, the variable doesn't match the account you signed in
with. In order of likelihood:

1. **The variable is missing, mistyped, or quoted.** Portainer → your
   BillFlow stack → *Editor*: make sure
   `BILLFLOW_ADMIN_EMAIL=you@gmail.com` (exactly the Google address you sign
   in with, no quotes) is present, then **Re-deploy**. The container start-up
   log prints `admin access ensured for 1 user(s)` when it matches, or a
   warning listing every account email it can see when it doesn't.
2. **Match but sign in again.** If the variable is correct, simply sign out
   and back in — the login hook grants access on the spot.
3. **One-off grant from the console (works on any image version).**
   Portainer → Containers → the BillFlow container → *Console*:
   ```sh
   python manage.py shell -c "from django.contrib.auth.models import User; u=User.objects.order_by('pk').first(); u.is_staff=u.is_superuser=True; u.save(); print('promoted', u.email or u.username)"
   ```
   That promotes your first account immediately (no redeploy). To see every
   account email on the deployment:
   ```sh
   python manage.py shell -c "from django.contrib.auth.models import User; print(list(User.objects.values_list('email', flat=True)))"
   ```
4. **Self-healing fallback**: at every container start and sign-in, if the
   variable matches nobody (or is unset), BillFlow promotes the **oldest
   account** (the first that ever signed up — you on this instance) and logs
   a warning explaining how to pin a specific address. You can't be locked
   out of `/admin/`.

## Troubleshooting: "failed to read stack.env: Invalid template"

Docker Compose treats `$` inside environment-variable **values** as the start
of a variable template (that is how `${VAR}` references work). If a value you
set in Portainer — e.g. a password-generator secret — contains `$`, the deploy
fails with `Invalid template: "${...}"`.

Any one of these fixes it:

- **Use a value without `$`** — e.g. run
  `python -c "import secrets; print(secrets.token_urlsafe(50))"`
  (base64url: only letters, digits, `_` and `-`), or a long hex string from
  your password manager.
- **Or escape every `$` in the value as `$$`.**
- **`DJANGO_SECRET_KEY` specifically: just delete the variable** — the app
  auto-generates a strong key and persists it in the data volume (see
  `billflow/settings.py`).

## Troubleshooting: Google sign-in fails with "Error 400: redirect_uri_mismatch"

Google only redirects to URIs that are registered **exactly** on your OAuth
client. The app builds its callback from the incoming request, so the URL it
sends is:

```
https://YOUR-DOMAIN/accounts/google/login/callback/
```

**To see what was actually sent:** on Google's error page click
*see error details / Error details* — it shows the exact `redirect_uri`.
That string must match a registered URI character-for-character (scheme, host,
port, path, trailing slash).

Common causes:

- **The production URI was never added** — registering only
  `http://localhost:8000/...` (for local dev) is not enough.
- **`http://` vs `https://`** — behind Cloudflare or any TLS-terminating proxy
  the app needs `DJANGO_SECURE_PROXY_SSL_HEADER=1` in the stack environment,
  otherwise Django builds `http://YOUR-DOMAIN/...` and Google always rejects
  that for anything except localhost. After changing a stack variable,
  *Recreate / re-deploy* the stack for it to take effect.
- **`www.` vs bare domain** — `https://www.example.com/...` and
  `https://example.com/...` are different URIs; register the one your browser
  actually shows.
- **Wrong path** — it must end with `/accounts/google/login/callback/`
  (with trailing slash), not `/accounts/google/callback/` or `/oauth2/...`.
- **A port in the URL** — if you access the app as `:8090`, the registered URI
  must include it. Plain-HTTP + raw-IP never works (except `localhost`).

**Fix:** Google Cloud Console → *APIs & Services → Credentials* → click your
OAuth 2.0 Client ID → *Authorized redirect URIs* → add the exact URI(s) →
*Save* (propagation usually takes a few minutes). While there, also make sure
`DJANGO_CSRF_TRUSTED_ORIGINS=https://YOUR-DOMAIN` is set in the stack — the
callback POST needs it too, and without it you'd next see a CSRF error.

## Running the tests

```powershell
python manage.py test tracker
```

Covers: anonymous redirect to sign-in, the styled login page (with and without
Google credentials configured), sign-out flow, the app page bootstrap, and the
state API (auth required, payload validation, save + round-trip).

## Production notes (when you deploy)

- Set `DJANGO_SECRET_KEY` and `DJANGO_DEBUG=False`.
- Add your real domain to `ALLOWED_HOSTS`, update the Site record (or
  `python manage.py shell`) and register the matching redirect URI in Google
  Cloud Console (`https://yourdomain/accounts/google/login/callback/`).
- Serve behind HTTPS; switch SQLite for PostgreSQL if you expect heavy use.
