# CapellaBridge

**Capella scores in. Editable MuseScore scores out.**

CapellaBridge is a private, self-hosted web converter for Capella score files. Upload a **.capx** or **.cap**, choose a layout, and download an editable **MuseScore (.mscz)** score, **MusicXML**, a printable **PDF**, and a first-page preview. No login, account, external API, or cloud conversion service.

![CapellaBridge desktop interface](docs/screenshot.png)

<details>
<summary>Mobile interface</summary>

![CapellaBridge mobile interface](docs/mobile.png)

</details>

## How it works

1. **Drop files** into the browser (batch uploads supported).
2. **Choose a layout** — Balanced (recommended), Compact, or Original system breaks.
3. **Convert and download** the score, MusicXML, PDF and conversion report. Upload progress is measured; engraving progress is shown as an activity indicator.

Original files are discarded after processing. Downloadable results expire by default after 24 hours. Jobs and settings are stored in a persistent Docker volume.

### Notation fidelity

CapellaBridge reads CapXML 2.0 notation as **structured musical events**, not screenshots or PDFs. It preserves score parts and voices, notes and chords, clefs, key and time changes, rests, exact tuplets, lyrics, ties, slurs, repeats, section numbers and supported articulations. The conversion report identifies features that require manual review.

**Version 1.7.0** fixes simultaneous repeat-end and repeat-start boundaries (including transitions across Capella systems), creates visible accent and staccato markings, adds explicit tuplet grouping and supported graphical trill/glissando marks, and fixes literal subtitle break tags in exported MuseScore files.

An **.mscz** is verified as an actual MuseScore score, not merely a readable ZIP. The test pipeline imports a generated score using MuseScore Studio 4 after MuseScore 3 export. Very unusual Capella drawing objects can still require manual cleanup: different engraving engines cannot guarantee identical page breaks, graphical placement, or playback for every source file.

**Legacy .cap:** Uses MuseScore's legacy Capella importer. This pathway is best-effort; the native CAPX interpretation is more capable.

## Deployment: Synology Container Manager / Docker Compose

CapellaBridge provides a public prebuilt Linux **amd64** Docker image at `ghcr.io/shuach613/capellabridge:latest`. The configuration below exposes it on **port 9595**, with persistent application data. It avoids CPU quota controls that some Synology kernels do not support.

Create a folder such as `/volume1/docker/capellabridge`, open **DSM → Container Manager → Project → Create**, and paste the complete configuration:

```yaml
services:
  capellabridge:
    image: ghcr.io/shuach613/capellabridge:latest
    platform: linux/amd64
    container_name: capellabridge
    restart: unless-stopped
    ports:
      - "9595:8080"
    volumes:
      - capellabridge_data:/data

volumes:
  capellabridge_data:
```

Start the project, then visit **http://NAS-IP:9595**. The webpage opens directly—there is **no application sign-in**.

> **Security:** Anyone who can access the service can upload files and use CPU resources. Do not expose port 9595 directly to the public internet. For remote use, protect it with a VPN or authentication/access restrictions on your reverse proxy.

### Updating an existing NAS installation

From SSH, in the directory containing your Docker Compose configuration:

```bash
cd /volume1/docker/capellabridge && docker compose pull && docker compose up -d --force-recreate && curl -s http://127.0.0.1:9595/health
```

This pulls the latest published image and recreates the existing container **without deleting the named volume**. Do not use `docker compose down -v` unless you deliberately intend to remove stored results.

### Available endpoints

| Endpoint | Description |
| --- | --- |
| `/` | Browser conversion interface |
| `/health` | Health, version, MuseScore engine availability |
| `/api/info` | Supported formats, upload limits, application version |
| `/api/convert?layout=balanced` | Multipart POST with one or more `files` |
| `/api/files/...` | Temporary score, PDF, MusicXML and report downloads |

## Development

Requires Python 3.12, dependencies in `requirements.txt`, MuseScore 3 CLI and Poppler for the full web service. For local parser checks:

```bash
python -m pip install -r requirements.txt pytest httpx
python -m pytest -q
```

Run the browser UI locally with `uvicorn app.main:app --host 127.0.0.1 --port 8080` (requires MuseScore for successful conversions). The Playwright script `scripts/capture_ui.py` generates the README screenshots from the **real HTML/CSS frontend**, using a local stub for the engine-status API.

Every pull request runs unit tests, builds a container, performs actual conversions in all three layout modes, and checks a native score with MuseScore Studio 4. Pushes to `main` publish `ghcr.io/shuach613/capellabridge:latest` through GitHub Actions.

## Project layout

```text
app/
  converter.py        CapXML event parser → MusicXML
  engraving.py        Native MuseScore print/layout styles
  score_validation.py MSCZ integrity validation
  musescore.py        MuseScore and PDF rendering
  main.py             FastAPI upload/download service
  static/             Responsive web UI
scripts/              Smoke tests and reproducible screenshots
tests/                CAPX and MuseScore regression coverage
.github/workflows/    Tests, container publication, screenshots
Dockerfile
docker-compose.yml
```

---

*CapellaBridge is an independent project and is not affiliated with capella-software AG or MuseScore.*
