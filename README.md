# wiki-server

A tiny LAN HTTP server for a [Karpathy-pattern knowledge wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f): a folder of interlinked markdown pages (YAML frontmatter, `[[wikilinks]]`) served to every machine on the network, so agents never keep local copies — they read, search, follow links, and upsert pages against one store.

One file, Python stdlib only, no database: the graph **is** the files. Writes are per-page atomic (`tmp` + rename); two clients editing different pages cannot collide.

## API

| Route | What it gives a client |
|---|---|
| `GET /health` | `{"status": "ok", "pages": N}` |
| `GET /index` | the wiki's index.md (coarse entry point; navigate by links) |
| `GET /schema` | SCHEMA.md — the conventions every writer must follow |
| `GET /search?q=terms` | pages containing all terms, with titles + matching lines |
| `GET /page?path=dir/name.md` | the page's markdown |
| `GET /neighbors?path=...` | `{"out": [links], "in": [backlinks]}` — the local map for graph walking |
| `POST /page` | JSON `{"path", "content", "note"}` — create/update one page; appends `note` to log.md and adds missing pages to index.md |

Paths must be under `entities/`, `concepts/`, `comparisons/` or `queries/` (plus the special `index`/`schema`/`log` reads). No auth — run it LAN-internal.

## Run

```
docker run -d -p 8000:8000 -v /path/to/wiki:/wiki ghcr.io/haasanen/wiki-server:v0.1.0
```

Compose service:

```yaml
  wiki:
    image: ghcr.io/haasanen/wiki-server:v0.1.0
    volumes:
      - /ssd/wiki-data:/wiki
    ports:
      - "8000:8000"
    restart: unless-stopped
```

Env: `WIKI_DIR` (default `/wiki`), `WIKI_PORT` (default `8000`).

## Releases

Tags `v*` build and publish `ghcr.io/haasanen/wiki-server:<tag>` via GitHub Actions (Dockerfile + CI kept here; the wiki content itself is a plain folder volume, not this repo).
