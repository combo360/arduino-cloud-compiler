# Publishing the i-Droid compile server

This folder is a complete, self-contained repository for the server. The
recommended host is Render (the original server also runs there), using the
`render.yaml` Blueprint. A Docker option for your own PC or VPS is at the end.

## What is in this folder

| Path | Purpose |
|---|---|
| `server/` | The server: FastAPI app (`app/`), `Dockerfile`, tests |
| `server/preinstall-libraries.txt` | Libraries baked into the image for everyone |
| `render.yaml` | Render Blueprint: one Docker web service with a 5 GB disk at `/data` |
| `.github/workflows/server.yml` | Runs the tests and a Docker build + test compile on every push |
| `DEPLOY.md` | This guide |

## Before you start

You need:

- a GitHub account (a **private** repository is fine);
- a Render account, signed in with GitHub;
- about 30 minutes; the first image build takes 10–20 minutes because it
  downloads the AVR core and 26 libraries.

Decide on the plan:

- **With a disk (`render.yaml` as supplied, `plan: starter`).** Libraries that
  users install, board cores you add, and the build cache survive restarts and
  deploys. Disks need a paid instance.
- **Free.** Edit `render.yaml`: set `plan: free` and delete the four `disk:`
  lines. Compiling still works, including the 26 shared libraries and the AVR
  core baked into the image, but the service sleeps when idle (the first
  request then takes about a minute) and anything installed while it runs is
  lost on restart.

Check current prices and limits on Render's pricing page before choosing.

## Step 1: Create the API keys

Create one key per person and one separate admin key for yourself. Run this
once per key (any computer with Python):

```bash
python -c "import secrets; print(secrets.token_urlsafe(24))"
```

Write them in the form the server expects:

- `IDROID_API_KEYS` = `KEY1:alice,KEY2:bob` (normal users; each gets their own libraries)
- `IDROID_ADMIN_KEYS` = `ADMINKEY:admin` (may install or remove board cores and board URLs)

Keep the keys out of the repository; you enter them in Render in Step 3.

## Step 2: Put the folder on GitHub

1. On github.com: **New repository** → name it, e.g. `idroid-server` →
   **Private** → **Create repository**.
2. Upload the contents of this folder (not the folder itself), so that
   `render.yaml` sits at the top of the repository:
   - with Git: `git init`, `git add .`, `git commit -m "i-Droid server"`,
     then follow GitHub's "push an existing repository" commands; or
   - in the browser: **uploading an existing file**, drag in everything,
     **Commit changes**. Make sure the hidden `.github` folder is included.
3. Open the **Actions** tab. The `server` workflow should go green within
   about 20 minutes. It runs the 33 tests, builds the image and compiles a test
   sketch, so a green run means the image builds before Render tries it.

## Step 3: Create the service on Render

1. Render dashboard → **New** → **Blueprint**.
2. Choose the `idroid-server` repository. Render reads `render.yaml` and shows
   one web service, `idroid-compile-server` (and its disk, if kept).
3. Fill in the two secret values when asked: `IDROID_API_KEYS` and
   `IDROID_ADMIN_KEYS` from Step 1.
4. **Apply**. Follow the build log; the first build is the slow one.
5. When it shows **Live**, copy the service URL, e.g.
   `https://idroid-compile-server.onrender.com` (yours may have a suffix).

## Step 4: Test it

In a browser, open `https://YOUR-URL/health`. It should show `{"status":"ok"}`.

Test a compile (replace the URL and key):

```bash
curl -s -H "X-API-Key: KEY1" -H "Content-Type: application/json" \
  -d '{"sketch":"#include <Servo.h>\nServo s;\nvoid setup(){s.attach(9);}\nvoid loop(){}","fqbn":"arduino:avr:uno"}' \
  https://YOUR-URL/v1/compile
```

Expect `"success":true` and a long `"hex"` value. A request without a key
returns 401, which is correct.

## Step 5: Point the app at it

- **For testing:** in i-Droid, menu → **Settings** → enter the server URL and
  your key → **Test connection**. Then compile Blink, open Examples, and try
  Auto Format.
- **For a release:** change the default in `lib/services/api_config.dart`
  (`kApiBaseUrl`) to your URL and build a new app version. Users still need
  their key in Settings.

Keep the original server running until users have moved to the new app
version: older copies of the app only know the old address and send no key.

## Replacing a server that is already running

When the repository still contains the old single-file server (`server/main.py`)
and the service was created by hand rather than from `render.yaml`, the service
works with the new code too: the layout (`server/` with a `Dockerfile`) is the
same. Set these before pushing, because a push redeploys the service:

1. Render → service → **Environment**: add `IDROID_API_KEYS`,
   `IDROID_ADMIN_KEYS`, and `IDROID_ALLOW_ANONYMOUS` = `true` while older app
   versions that send no key are still in use. Saving redeploys the old code,
   which ignores the new variables.
2. Render → service → **Settings**: clear any custom **Start Command** or
   **Docker Command**. The old image was often started with
   `uvicorn main:app --port 8000`; the new image has no `main.py` and takes the
   port from `$PORT` itself. Check **Root Directory** is `server` and
   **Dockerfile Path** is `server/Dockerfile`.
3. Push the new code. The auto-deploy builds the new image; the first build
   takes 10–20 minutes.
4. Run `verify-deploy.ps1` against the service URL (see Step 4), then switch
   `IDROID_ALLOW_ANONYMOUS` back to `false` once every user has a key, or leave
   it on if the app must stay usable without one. Anonymous users can compile
   but cannot install libraries or cores.

A disk is optional here: without one, compiles still work (the AVR core and the
shared libraries are baked into the image), but cores you install and libraries
installed for a user are lost on every restart.

## Updating later

- **Code changes:** push to GitHub; Render redeploys automatically.
- **Shared libraries:** edit `server/preinstall-libraries.txt` and push. On a
  service with a disk, the new list only reaches `/data` on first start, so
  also delete `/data/shared` (Render → Shell: `rm -rf /data/shared`) and
  restart.
- **More board cores:** install them from the app's Boards Manager with the
  admin key (needs the disk), or bake them into the image by editing the
  defaults of `ARG PREINSTALL_CORES` and `ARG PREINSTALL_URLS` at the top of
  `server/Dockerfile` (on a service with a disk, also delete `/data/arduino15`
  once so the new cores are copied). The ESP32 core adds well over 1 GB and
  needs a larger instance to compile reliably.
- **Keys:** edit `IDROID_API_KEYS` in Render → service → **Environment**;
  saving restarts the service.

## Settings you may want to change

Set these in Render → **Environment** (all optional):

| Variable | Default | Meaning |
|---|---|---|
| `IDROID_ALLOW_ANONYMOUS` | `false` | `true` lets people compile without a key (no installs) |
| `IDROID_AUTO_INSTALL_LIBRARIES` | `true` | Install the library for a missing `#include` for that user |
| `IDROID_COMPILES_PER_MINUTE` | `10` | Per key |
| `IDROID_MAX_CONCURRENT_COMPILES` | `2` | Raise only on a larger instance |
| `IDROID_COMPILE_TIMEOUT` | `120` | Seconds |

The full list is in `server/README.md`.

## If something goes wrong

| Symptom | Likely cause and fix |
|---|---|
| Build fails at `core install` or `lib install` | Arduino's download server was busy; **Manual Deploy → Clear build cache & deploy**. A failed single library only prints `WARNING` and does not stop the build. |
| Log says `/data is not writable` | The disk is mounted read-only or at a different path; check the disk's mount path is `/data`. |
| App says 401 / invalid key | Key typed wrongly in Settings, or not in `IDROID_API_KEYS` (check for spaces). |
| App says 403 when installing a board | Only admin keys can install cores. |
| First request very slow | Free plan waking up, or the first compile for a new board. |
| Compile fails with `Missing libraries for X.h` | The request had no key; add the key in Settings. |

## Running it on your own PC or VPS instead

With Docker installed:

```bash
cd server
docker build -t idroid-server .
docker run -d --name idroid --restart unless-stopped -p 8080:8080 \
  -v idroid-data:/data \
  -e IDROID_API_KEYS="KEY1:alice" -e IDROID_ADMIN_KEYS="ADMINKEY:admin" \
  idroid-server
```

On the same Wi-Fi the app can use `http://PC-IP:8080`. For use over the
internet, put it behind HTTPS (for example a reverse proxy such as Caddy or
nginx); without HTTPS the keys and sketches travel unencrypted.
