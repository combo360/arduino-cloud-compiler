# Cutover runbook — GitHub and Render

Step-by-step for replacing the old open server with this one, keeping the
existing service URL `https://arduino-cloud-compiler-u43r.onrender.com` so the
app does not need a new build.

**This repository is public. Never commit API keys.** The generated keys are in
`%USERPROFILE%\idroid-server-keys.txt` on your PC, outside both repositories.
`render.yaml` uses `sync: false` for the secrets, which makes Render prompt for
them instead of reading them from the file.

## Where things stand

| Item | State |
|---|---|
| `combo360/arduino-cloud-compiler` (public, branch `main`) | still the old single-file server (`server/main.py`) |
| Live service `arduino-cloud-compiler-u43r.onrender.com` | runs that old code, no auth (`/health` → `{"status":"ok","cli":true}`) |
| New server | prepared and committed locally, **not pushed** |
| Old code | still reachable at commit `f1b316f` if you need it back |

Order matters. **Do Phase 2 before Phase 4**: pushing or uploading redeploys the
service within seconds, and without the env vars every request — including
anonymous ones — returns 401, which breaks the app for anyone using it.

---

## Phase 1 — GitHub

Pick one path.

### Path A — the agent pushes (recommended)

Needs a fine-grained token, used once. This replaces the old files and adds the
new ones in a single commit.

1. GitHub → your avatar → **Settings**.
2. **Developer settings** → **Personal access tokens** → **Fine-grained tokens**
   → **Generate new token**.
3. Fill in:
   - **Token name**: `idroid-cutover`
   - **Expiration**: 7 days
   - **Resource owner**: `combo360`
   - **Repository access**: *Only select repositories* →
     `combo360/arduino-cloud-compiler`
4. **Repository permissions** → **Contents** → *Read and write*. Leave every
   other permission at *No access*.
5. **Generate token**, copy it, and paste it to the agent.
6. After the push succeeds, delete the token:
   **Settings → Developer settings → Fine-grained tokens → `idroid-cutover` →
   Delete**.

### Path B — upload in the browser (no token)

1. Extract `idroid-server-flat.zip` to a folder. It must contain `render.yaml`
   at the top level, **not** an `idroid-server/` wrapper folder.
2. Delete the old entry point: open
   `https://github.com/combo360/arduino-cloud-compiler/blob/main/server/main.py`
   → trash icon → **Commit changes**.
3. Open `https://github.com/combo360/arduino-cloud-compiler` → **Add file** →
   **Upload files**.
4. In Explorer, select everything inside the extracted folder (Ctrl+A) and drag
   it onto the page. Drag the `.github` folder too if you want the CI workflow —
   Explorer hides it until you enable **View → Show → Hidden items**.
5. **Commit changes** to `main`.
6. GitHub's uploader only adds and overwrites. If any file from the old server
   remains besides `main.py` (it should not), delete it the same way as step 2.

### Verify Phase 1

- [ ] `render.yaml` is visible at the repository root.
- [ ] `server/app/main.py` exists.
- [ ] `server/main.py` is gone.
- [ ] `server/Dockerfile` is the new one (starts with
      `FROM python:3.12-slim-trixie`).
- [ ] Optional: the **Actions** tab shows the `server` workflow. It runs the 33
      tests and a smoke compile, which is the earliest warning that the image
      will not build. It is not required for the deploy.

---

## Phase 2 — Render environment variables (do this first)

1. Open <https://dashboard.render.com/>, select the service behind
   `arduino-cloud-compiler-u43r.onrender.com` (named `arduino-cloud-compiler`
   plus a suffix).
2. Sidebar → **Environment**.
3. Add three variables (`Add Environment Variable`):

   | Key | Value |
   |---|---|
   | `IDROID_API_KEYS` | the `IDROID_API_KEYS` line from `idroid-server-keys.txt` |
   | `IDROID_ADMIN_KEYS` | the `IDROID_ADMIN_KEYS` line from `idroid-server-keys.txt` |
   | `IDROID_ALLOW_ANONYMOUS` | `true` |

   `IDROID_ALLOW_ANONYMOUS=true` keeps already-installed app versions working,
   because they send no key. Anonymous users can compile but cannot install
   libraries or cores. Turn it off in Phase 7 once your app has a key.
4. **Save changes**. Render redeploys the old code; it ignores these variables,
   so the site stays up.

---

## Phase 3 — Render service settings

Still in the service, sidebar → **Settings**:

1. **Docker Command** (under *Build & Deploy*): must be **empty**. This field
   overrides the image's `CMD`; your old image was started with
   `uvicorn main:app --host 0.0.0.0 --port 8000`, and the new image has no
   `main.py`. The new image reads `$PORT` itself.
   This is the single most likely reason a new deploy builds but never goes
   live.
2. **Root Directory** and **Dockerfile Path**: either
   - Root Directory **empty**, Dockerfile Path `server/Dockerfile`, or
   - Root Directory `server`, Dockerfile Path `Dockerfile`.
   Never `Root Directory = server` together with
   `Dockerfile Path = server/Dockerfile` — Render would look for
   `server/server/Dockerfile`. If unsure, clear Root Directory and set
   Dockerfile Path to `server/Dockerfile`.
3. **Auto-Deploy**: *On Commit*, so Phase 4 builds immediately.
4. **Health Check Path**: `/health` (optional, recommended). Render waits for a
   200 before routing traffic to the new instance.
5. **Instance type**: the free plan is enough to start. A **disk** mounted at
   `/data` needs a paid instance; without it, compiles still work (the AVR core
   and the 26 shared libraries are baked into the image) but cores and libraries
   installed at runtime are lost on every restart and sleep.

---

## Phase 4 — Deploy

Push the prepared commit (Path A) or commit the upload (Path B); either triggers
the build. Expect **10–20 minutes** for the first one.

Watch **Logs** for these lines in order:

```
#  apt-get install curl ca-certificates clang-format
#  arduino-cli  ... installed
#  arduino-cli core install arduino:avr
#  arduino-cli lib install
#  arduino-cli compile --fqbn arduino:avr:uno /tmp/warmup
#  Copying /opt/arduino15 to /data (first start)...
#  Uvicorn running on http://0.0.0.0:$PORT
```

A library that fails to install prints `WARNING:` and does not stop the build.

---

## Phase 5 — Verify

```powershell
cd C:\Users\ibrah\StudioProjects\idroid\idroid-server
.\verify-deploy.ps1 -BaseUrl https://arduino-cloud-compiler-u43r.onrender.com `
  -ApiKey <owner key> -AdminKey <admin key>
```

Every check must print `PASS`; the script exits 0 only then. It covers health, a
keyless 401, a real `Servo.h` compile, installed boards, examples, Auto Format
and per-user libraries.

Manual spot check:

```powershell
Invoke-RestMethod https://arduino-cloud-compiler-u43r.onrender.com/health
```

`{"status":"ok"}` — note it no longer includes `"cli":true`, which is how you
tell the new server from the old one.

---

## Phase 6 — App

1. i-Droid → **Settings** → enter the URL above and the `owner` key →
   **Test connection**.
2. Compile **Blink**, open **Examples**, run **Auto Format**.
3. For a release build, set `kApiBaseUrl` in `lib/services/api_config.dart`
   (line 16) and ship a new version.

---

## Phase 7 — Hardening (after the app has a key)

1. Set `IDROID_ALLOW_ANONYMOUS` to `false`. Save (restarts the service).
2. Give each person their own key in `IDROID_API_KEYS` (`key:name,key:name`) so
   libraries stay separate and rate limits apply per person.
3. Optional: add a disk at `/data` (paid) so installed cores and libraries
   survive restarts.
4. Optional: more board cores. Render passes environment variables to the build
   as Docker build arguments, so adding a variable named `PREINSTALL_CORES` with
   `arduino:avr arduino:samd` bakes SAMD in at the next build — no Dockerfile
   edit. ESP32 needs `PREINSTALL_URLS` as well and adds over 1 GB; it also wants
   a larger instance to compile reliably.
5. Revoke the GitHub token if you used Path A.

---

## Phase 8 — Rollback

- **Fastest**, no git: Render → **Deploys** → pick the last good deploy →
  **Redeploy**. This reruns the previous image.
- **Code**: `git revert` the cutover commit and push, or restore
  `server/main.py` from commit `f1b316f`.
- The old server accepted no keys, so rolling back also means the app's key is
  ignored — that is harmless, the extra header is simply unused.

---

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Build fails: `Dockerfile not found` / `failed to read dockerfile` | Root Directory and Dockerfile Path disagree (Phase 3.2) |
| Build succeeds, deploy fails with `/bin/sh: 1: uvicorn: not found` or `main:app` errors | A **Docker Command** is still set; clear it (Phase 3.1) |
| Service returns 401 for everything | `IDROID_API_KEYS` not saved, or `IDROID_ALLOW_ANONYMOUS` still `false` |
| Build fails at `core install` / `lib install` | Arduino's download server was busy: **Manual Deploy → Clear build cache & deploy** |
| `ERROR: /data is not writable` | A disk is mounted read-only or at another path; mount it at `/data` |
| App: `Missing libraries for X.h` | The request had no key, so no install is allowed — add the key in Settings |
| Compile fails for SAMD/ESP32 boards | Those cores are no longer baked in; install them from Boards Manager with the admin key (needs a disk), or add `PREINSTALL_CORES` (Phase 7.4) |
| First request takes ~50 s | Free instance waking up; a paid instance stays warm |
| App: 403 when installing a board | Only admin keys may install cores |
