# CapellaBridge 1.0

**Capella (`.capx` / `.cap`) → editable MuseScore (`.mscz`) + MusicXML + PDF**, through a private web interface on your own NAS.

A single Docker service designed for Synology. The interface is inspired by Capella's light-blue score workspace and MuseScore Studio's dark editing environment, while using entirely original CapellaBridge branding. This is an **independent project** and has no affiliation with capella-software AG or MuseScore.

> **Important:** CAPX parsing, the FastAPI UI, and mocked web conversion are tested locally. The supplied GitHub Actions workflow also performs a **real MuseScore CLI conversion** in Docker before publishing; that full container test must pass on GitHub before the image is available. Results may require manual notation review. The music's editability takes priority over exact typography or pagination.

## Included in version 1.0

- Responsive browser interface; drag and drop multiple `.capx` or `.cap` files; choose between balanced, compact, and original-system engraving.
- Native CapXML 2.0 parser for `.capx` with separate staves and voices, notes, rests, common key/time/clef changes, many repeats, ties, slurs and articulations.
- Legacy `.cap` through MuseScore 3's binary import support (**best effort**, not a custom binary parser).
- Real `.mscz` via headless MuseScore 3; `.musicxml`, PDF, and a first-page PNG preview; per-file report and whole-batch ZIP.
- HTTP Basic authentication, HTTPS reverse-proxy compatibility, bounded upload sizes and ZIP extraction, non-root process, automatic job cleanup.
- GitHub Actions pipeline: automated unit tests → Docker build → real upload/conversion smoke test → publish to **GitHub Container Registry (GHCR)**.
- Release tags: `:latest` for pushes to `main`; `:sha-...` for commits; `:1.0.0` and `:1.0` when you push `v1.0.0`.
- Image is built on GitHub for **linux/amd64**, compatible with x86-64 Synology NAS devices. The NAS only downloads and runs the image.

## 1 · Create the GitHub repository

1. Create an empty repository called `capellabridge` under your own GitHub account. Choose **public** if you want others to use it and want the NAS to pull images without a registry token. Do **not** initialize it with a README (the project includes one).
2. Extract this release ZIP onto your Mac or PC, open Terminal in the extracted `capellabridge-1.0` directory, and run (replace `YOUR_GITHUB_USERNAME`):

   ```bash
   git init -b main
   git add .
   git commit -m "Release CapellaBridge 1.0"
   git remote add origin https://github.com/YOUR_GITHUB_USERNAME/capellabridge.git
   git push -u origin main
   ```

   If you prefer GitHub CLI and have it authenticated, you can create and push from the directory with:

   ```bash
   gh repo create capellabridge --public --source=. --remote=origin --push
   ```

   In that case initialize Git and commit first (`git init -b main`, `git add .`, `git commit ...`), but do not add the remote/push twice.

3. Under your repo's **Actions** tab, open **Verify and publish Docker image**. For every push to `main`, GitHub runs unit tests, builds the Docker image, performs an end-to-end live MuseScore conversion, and *only then* pushes to GHCR. Nothing is published if the smoke test fails.
4. Once the workflow succeeds, go to your GitHub profile or repo's **Packages** listing, open the `capellabridge` image, choose **Package settings → Change visibility → Public** if it was created private. GHCR packages can start private even for a public repo. The public image can then be pulled by your NAS without `docker login`.

The image name will be:

```text
ghcr.io/YOUR_GITHUB_USERNAME/capellabridge:latest
```

The workflow lowercases the actual GitHub owner and repository for the image path. In your `.env` always use a **lowercase** image path.

### Optional numbered release

```bash
git tag v1.0.0
git push origin v1.0.0
```

Once the tagged build passes, GHCR also has `:1.0.0` and `:1.0`. Pin the NAS `.env` to a version tag for predictable updates, or use `:latest` for updates on every new push to `main`.

## 2 · Install on Synology (no NAS build)

Put **only these two files** from the project into `/volume1/docker/capellabridge/` on the NAS:

- `docker-compose.yml`
- `.env` (copy `.env.example` and fill in the values below)

Example `/volume1/docker/capellabridge/.env`:

```dotenv
CAPELLABRIDGE_IMAGE=ghcr.io/your_github_username/capellabridge:latest
CAPELLABRIDGE_USER=admin
CAPELLABRIDGE_PASSWORD=REPLACE_WITH_A_RANDOM_LONG_PASSWORD
CAPELLABRIDGE_PORT=8765
CAPELLABRIDGE_BIND_IP=127.0.0.1
CAPELLABRIDGE_TTL_HOURS=24
CAPELLABRIDGE_MAX_UPLOAD_MB=15
CAPELLABRIDGE_MAX_BATCH=12
```

Pick a unique password. To create one on macOS or Linux: `openssl rand -base64 36`. **Never commit the real `.env`** (it is in `.gitignore`).

Connect to the NAS with SSH and run:

```bash
cd /volume1/docker/capellabridge
docker compose pull
docker compose up -d
docker compose ps
docker compose logs --tail=80
```

This Compose file uses **`image:` instead of `build:`**; `pull_policy: always` instructs Compose to fetch updates when run. Your NAS does not compile the application.

By default the container listens on **`127.0.0.1:8765`** on the NAS. This intentionally does not allow access directly from another device. If you need trusted LAN-only access without a reverse proxy, set `CAPELLABRIDGE_BIND_IP=0.0.0.0`, review your NAS firewall, and open `http://NAS-IP:8765`. Do **not** expose that HTTP port publicly.

### External access: Synology reverse proxy

In **DSM → Control Panel → Login Portal → Advanced → Reverse Proxy**, add a rule:

| Item | Value |
|---|---|
| Source | HTTPS, `capella.shua.ch`, port 443 |
| Destination | HTTP, `127.0.0.1`, port 8765 |
| Certificate | Valid HTTPS certificate for the hostname |

Point the DNS record to your existing protected NAS entry point. Only expose 443 through your existing HTTPS setup; do not directly port-forward 8765. HTTP Basic credentials are safe **only when HTTPS is used**. Configure firewall, rate limiting, and optional additional identity-proxy authentication as appropriate.

### Private GitHub package?

If you keep the GHCR image private, create a GitHub personal access token with the minimum necessary `read:packages` permission and log in **on the NAS** to `ghcr.io` once using `docker login` with `--password-stdin`. Do not save the token in Compose or your public repo. Public GHCR visibility is simpler for this open-source project; it does **not** make your protected CapellaBridge website public without your own network/proxy configuration.

## 3 · Updating and automatic deployment

**Building on GitHub does not automatically restart Docker on Synology.** Choose one of these two options:

- **Manual update:** SSH to the NAS and run:

  ```bash
  cd /volume1/docker/capellabridge
  docker compose pull
  docker compose up -d --remove-orphans
  ```

- **Automatic pull/deploy:** In DSM **Control Panel → Task Scheduler**, create a scheduled root task that runs these same commands nightly (or on your preferred schedule). For example:

  ```bash
  cd /volume1/docker/capellabridge && /usr/local/bin/docker compose pull && /usr/local/bin/docker compose up -d --remove-orphans
  ```

  Verify your Synology Docker binary location first with `command -v docker` and substitute the returned path (some DSM installations use `/usr/local/bin/docker`, others a different location). The repository includes `scripts/nas-update.sh` for installations where you copy the complete folder onto the NAS.

An upgrade replaces the running container only after Compose downloads the image; if the new GitHub build fails, it will not be published. You can pin a particular tag such as `:1.0.0` in `.env` if you want to approve upgrades manually.

## Local development and checks

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt pytest httpx
CAPELLABRIDGE_PASSWORD=local-only-secret CAPELLABRIDGE_DATA=./data uvicorn app.main:app --port 8080 --reload
python -m pytest -q
```

The local Python development server does **not** include MuseScore unless you install MuseScore 3 and `pdftoppm` separately. To build the full container on a Docker-enabled development PC:

```bash
docker build -t capellabridge:local .
docker run -d --rm --name capellabridge-test \
  -p 127.0.0.1:18765:8080 \
  -e CAPELLABRIDGE_USER=smoketest \
  -e CAPELLABRIDGE_PASSWORD=smoke-password-for-ci \
  capellabridge:local
python scripts/smoke_test.py http://127.0.0.1:18765
docker stop capellabridge-test
```

The `scripts/smoke_test.py` script checks real browser API input and validates generated MSCZ, PDF, XML, preview PNG and ZIP files.

## Musical conversion limits

- The native parser targets the **CapXML 2.0** schema (as in the supplied Capella 8 Brandywine example). Other versions may require additional work.
- Binary `.cap` relies on MuseScore 3's importer; not all historical Capella versions are guaranteed to open.
- Some trills, free graphical annotations, font-specific glyphs, lyrics, tuplets, unusual mid-score instruments, complex cross-staff beams and multi-voice offsets are not yet fully mapped.
- First/second endings are inferred from graphical `volta` data. The conversion report identifies known uncertainties; visually check repeats and ornaments after import.
- Capella and MuseScore number pitch octaves differently; the native conversion handles the octave difference.
- The server uses Debian's **MuseScore 3** command-line program to generate an editable `.mscz`; when opened in a recent MuseScore Studio release, the newer engraving engine may move systems, slurs, line breaks or text.
- Page design is intentionally secondary to a clean, editable score. The balanced layout is recommended.

This software should not be relied upon as a definitive performance edition without reviewing the result.

## Security and licensing

The app runs as non-root, restricts Linux capabilities, bounds uploads and memory usage, expires conversion output after the configured TTL, and requires username/password for its UI and API. Password authentication is HTTP Basic, so **HTTPS is mandatory for internet access**. Consider additional reverse-proxy access controls if it is available to untrusted users. The unprotected `/health` response shows only readiness/version information.

The code is licensed under the bundled `LICENSE`. The image bundles MuseScore 3 from Debian, which has its own GPL-related obligations; if redistributing the image publicly, review compliance with its GPL and source availability terms. Capella and MuseScore names are used only to describe file compatibility, not to imply endorsement.
