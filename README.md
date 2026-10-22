# CapellaBridge

Convert Capella scores (`.capx`, best-effort legacy `.cap`) into editable MuseScore files (`.mscz`) in a private self-hosted Docker web interface. Also export MusicXML, PDF, PNG previews, and batch ZIPs.

![CapellaBridge interface](capellabridge-1.0%202/docs/screenshot.png)

## Quick deployment on Synology

The root [docker-compose.yml](docker-compose.yml) is ready for Synology Container Manager → Project. No `.env` file required. The first-run administrator credentials are generated automatically; obtain them in the container logs and change the password in Account Settings.

Once the [Docker publishing workflow](.github/workflows/docker-publish.yml) has succeeded and the GHCR package is public, run:

```bash
docker compose pull
docker compose up -d
docker logs capellabridge
```

The service uses port **8765**. Store the Docker Compose project in `/volume1/docker/capellabridge` if using SSH for updates. Keep the named Docker volume: it holds the administrator account and temporary results.

## Automatic image builds

Each push to **main** triggers unit tests, an image build, an actual converter smoke test, and—if every step succeeds—publication of:

`ghcr.io/shuach613/capellabridge:latest`

See [GitHub Actions](https://github.com/shuach613/capellabridge/actions). The NAS does not automatically redeploy when the registry image changes: run `docker compose pull && docker compose up -d` to update.

## Features and limitations

- Four main outputs: MuseScore MSCZ, MusicXML, PDF, and PNG
- Multi-file upload, bulk download, conversion reports
- Original score layout is interpreted, but clean editability is prioritized
- Temporary conversion outputs expire after approximately 24 hours; source uploads are removed after processing
- Access should be protected by HTTPS reverse proxy or VPN when used remotely
- Not affiliated with capella-software or MuseScore

**Source code:** The current active app and Dockerfile are in [capellabridge-1.0 2](capellabridge-1.0%202/). This folder name is retained temporarily to match the previously uploaded project. Edit this directory to trigger updated builds. The other `capellabridge-1.0` folder is an older duplicate and is **not** built by the workflow.

![Mobile interface](capellabridge-1.0%202/docs/mobile.png)
