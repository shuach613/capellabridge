# CapellaBridge

**From Capella notation to editable MuseScore scores.**  
An independent, self-hosted converter with a straightforward upload-and-download interface.

![CapellaBridge desktop](docs/screenshot.png)

<p align="center">
  <strong>CAPX / CAP</strong> &nbsp;→&nbsp; <strong>MuseScore · MusicXML · PDF</strong>
</p>

## Why CapellaBridge?

CapellaBridge is for musicians and arrangers who need to continue working with scores originally created in Capella. Upload a score in your browser, choose a page layout, and receive an editable **MuseScore (.mscz)** file alongside **MusicXML (.musicxml)** and a PDF preview.

- **No application account**: a single simple web interface; no registration.
- **Private deployment**: the conversion engine runs on your Docker host, not an external conversion API.
- **Editable output**: staves, pitches, voices, meters, and supported music symbols are translated into notation data, not merely rendered as images.
- **Batch conversion**: upload up to 30 files, view results independently, or download a combined ZIP.
- **Transparent validation**: warnings and a JSON report explain notation elements that may need manual review.
- **Progress you can see**: a measured upload indicator and an animated engraving stage.

<details>
<summary>Mobile screenshot</summary>

![CapellaBridge on mobile](docs/mobile.png)

</details>

## Quick start — Synology NAS / Docker Compose

A prebuilt **Linux amd64** image is published automatically from the main branch at `ghcr.io/shuach613/capellabridge:latest`. This deployment uses host port **9595** and a persistent named volume.

Create a Container Manager project (or a `docker-compose.yml` file) with:

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

Start the project, then open **http://YOUR-NAS-IP:9595**. No `.env` file, credentials, database setup, or application sign-in is required.

To update an existing Synology installation over SSH:

```bash
cd /volume1/docker/capellabridge && docker compose pull && docker compose up -d --force-recreate && curl -s http://127.0.0.1:9595/health
```

This recreates the container with the latest image while retaining the named volume. **Never run `docker compose down -v` unless you intend to erase its persistent data.**

> **Network security:** CapellaBridge intentionally has no login. Anyone able to reach the interface can use conversion resources. Keep the port on a trusted network, or protect remote access with a VPN or an authenticated reverse proxy. Do not publish port 9595 openly to the internet.

## Converting a score

1. Drop one or more **.capx** or legacy **.cap** files into the upload area.
2. Select **Balanced** (recommended), **Compact**, or **Original systems**.
3. Convert, preview the results, and download MuseScore, MusicXML, PDF or the full batch ZIP.

Every score for which MuseScore creates a genuine native archive receives an
MSCZ. CapellaBridge separately certifies the result by comparing pitches,
durations, voices, tuplets, ties, slurs, articulations, dynamics, hairpins,
tempo, breath marks, repeats, endings, key and time signatures, navigation and
transposition. A score that passes is labelled **verified**. If a structural or
musical check cannot certify it, the MSCZ is retained and labelled **manual
review**, with the full diagnostics in its JSON report. A failure to render a
PDF or preview likewise does not remove an already-created editable score.

After a batch finishes, use **Start over** to clear the queue and results
before selecting another batch.

Results are temporary and normally expire after 24 hours. Uploaded originals are discarded following processing. The application stores its job files in the Docker volume.

| Layout | Intended behavior |
| --- | --- |
| **Balanced** | Comfortable spacing and flexible system breaks; recommended for most scores |
| **Compact** | Denser engraving to reduce empty space |
| **Original systems** | Request source Capella system breaks; may fall back to Balanced when the result would be impractically long |

## Supported notation and fidelity

CAPX files are interpreted from **CapXML 1.0, 2.0, 3.0 and 4.0** into **MusicXML**, then converted by the installed MuseScore engine to native `.mscz`, PDF, and preview images. Supported features include notes and rests, chords, multiple voices, grace notes, ties, slurs, tuplets, written time signatures and changes, key changes, clefs, accidentals, staccato and accents, repeat signs, first/second endings, variation headings and first-page credits. Known Capella3-font symbols are translated into **editable dynamics** (p, pp, f, ff, mf, mp and related markings), **trills and fermatas**, plus D.C., D.S., Segno and Coda navigation where semantically identified in the source. Textual directions and credits are preserved from page objects or note-attached graphics when recognized; compact rich-text (RTF) navigation objects are decoded conservatively, with recognized Capella3 glyphs translated to readable navigation instructions.

The converter validates native MuseScore score structure and rhythmic measure lengths, then runs a **MuseScore Studio 4** cross-version reopen/export test during CI.

### Examples covered by the regression suite

The following independently provided Capella scores were used to identify general compatibility cases. The repository includes **small synthetic regression fixtures**, not the original copyrighted music files.

| Source-score example | Original pages | Important notation characteristics |
| --- | ---: | --- |
| **Vaudois** | 2 | Repeats, alternative endings, red page-level D.C. al Fine and attached Fine, three instruments |
| **Waggis** | 3 | Numerous triplets, grace notes, slurs and ties, section numbers, a fourth piccolo appearing only in the last system |
| **Calvados (mit TS)** | 4 | 2/4 ↔ 6/8 transitions, seven- and eight-measure rests, first/second endings, performance text and late repeats |
| **Fyyrvogel** | 5 | CapXML 3.0, changing meters, multipart notation, graphical title logo, four piccolos transitioning to a solo Zier staff |
| **Nunnefirzli** | 4 | CapXML 1.0, multiple voices within staves, expression hairpins, slurs, dialect section headings and 2/4 |
| **z'Basel am mym Rhy** | 3 | Fourth-part credit, a fourth piccolo entering in later systems, staccato, ties, alternative endings, triplets, and last-page editorial attribution |
| **Ai du scheene – 5 Variationen** | 6 | CapXML 3.0, four piccolos, changing time signatures, trills, fermatas, source-labeled glissando and thematic headings |
| **Alte Kameraden 94** | 5 | CapXML 3.0, dynamically marked four-part score with p/f/mf glyphs, solo entries, ties and repeat endings |
| **Alte Kameraden Tutti (Bajass)** | 5 | Older binary .cap file; parsed by MuseScore's legacy import engine, so results require comparison with the original PDF |
| **Uncle Sam** | 4 | Parenthesized arranger credit, medley tune labels and performance words, RTF D.S. al Segno / Coda navigation |
| **Waggis** | 3 | Copyright, variable active staff count, nine numbered sections, tuplets, slurs and first/second endings |
| **Sperber** | 3 | Four-part score and dated dedication, composer metadata separate from full printed author line |
| **Ryslaifer / Saggodo / Retraite** | 4 / 4 / 1 | Older binary .cap scores; requires the legacy MuseScore importer and individual visual checking |

The converter preserves part identities when staves appear, disappear, or are replaced; reads page-level titles and composers; maintains written meter notation (for example **2/4**, not **1/2**); and expands multimeasure rests across their actual spans. It also interprets common Capella graphical crescendo and diminuendo hairpins and keeps named variation headings above the relevant measures. Where a Capella title exists only as an embedded WMF graphic, it uses the uploaded filename as editable score text and retains readable composer/byline information.

**Important:** A correctly generated MSCZ and valid MusicXML are not a guarantee of a facsimile engraving. Dynamic playback markings, complex D.C./D.S. navigation, source-specific fonts, codas and editorial markings should be verified manually in MuseScore, especially in binary .cap files. Exact page counts, custom music fonts, beams, complex graphical objects and advanced ornaments may differ from the original PDF. Always compare the exported PDF and playback with the source before professional publication. Supported legacy `.cap` import uses MuseScore’s own older importer and is inherently best effort.

## Development and verification

Requires Python 3.12 for source development. Install the Python dependencies and run unit tests with:

```bash
python -m pip install -r requirements.txt pytest httpx
python -m pytest -q
```

The CI pipeline runs a growing synthetic notation regression suite, builds the Linux Docker image, converts synthetic CAPX scores through MuseScore 3, checks output file integrity, and verifies that MuseScore Studio 4 can reopen and re-export a generated MSCZ. Original user-owned reference scores and PDFs are not committed to the repository; a successful CI run is not a guarantee of pixel-identical source-PDF layout. Main-branch builds publish the prebuilt image to GitHub Container Registry.

The browser UI lives in `app/static/`. Screenshots are captured from the **real responsive interface** using `scripts/capture_ui.py` (Playwright), rather than generated marketing mockups.

### Repository map

```text
app/
  converter.py          CapXML 1.0–4.0 parser → structured MusicXML
  engraving.py          MuseScore printing and layout adjustments
  score_validation.py   Native-score integrity checks
  semantic_audit.py     Playback-level MusicXML ↔ MSCX equivalence checks
  musescore.py          MuseScore CLI and PDF rendering
  main.py               FastAPI routes, upload handling and downloads
  static/               Desktop/mobile web interface
tests/                  Notation and compatibility regressions
scripts/                Docker smoke tests and screenshot capture
docs/                   Actual desktop and mobile screenshots
.github/workflows/      Automated verification and GHCR image publication
Dockerfile
docker-compose.yml
```

### API

| Endpoint | Use |
| --- | --- |
| `/` | Conversion webpage |
| `/health` | Application version and engine health |
| `/api/info` | Upload limits and renderer details |
| `POST /api/convert?layout=balanced` | Multipart request with one or more `files` fields |
| `POST /api/convert?layout=balanced&background=1` | Start a background batch job and return a `status_url` without holding the upload request open |
| `GET /api/jobs/{job_id}` | Poll a background job until its per-file results and optional `batch_url` are ready |
| `/api/files/…` | Time-limited download of generated artifacts |

---

CapellaBridge is an independent project; it is not affiliated with **capella-software AG** or **MuseScore**. Original scores and their publication rights remain with their respective owners.
