# Commit Guide

This repository is organized as a small monorepo for the Photoshop render worker system.

## Commit These

```text
.gitignore
README.md
docs/
photoshop_render_server/
photoshop_uxp_worker/
```

## Do Not Commit These

```text
photoshop_render_server/.env
photoshop_render_server/.venv/
photoshop_render_server/data/
photoshop_render_server/*.jpg
photoshop_render_server/*.jpeg
```

The root Vite app files that existed before this project are ignored so `git add .` can be used safely for this monorepo.

## First Commit

```bash
git checkout -b codex/photoshop-render-worker
git add .
git status
git commit -m "Scaffold Photoshop render worker"
```

## Suggested Branches

```text
codex/add-mcp-server
codex/add-sqlite-jobs
codex/add-signed-upload-url
codex/improve-worker-ui
codex/package-uxp-worker
```

