# wiki-server

A small LAN HTTP server for a [Karpathy-pattern knowledge wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f): a folder of interlinked markdown pages (YAML frontmatter, `[[wikilinks]]`) served to every machine on the network, so agents never keep local copies — they read, search, walk links, and edit pages against one store.

One file, Python stdlib only, no database: the graph **is** the files. Writes are per-page atomic (`tmp` + rename) under per-path locks; two clients editing different pages cannot collide, and an `If-Match`-style `base` sha makes a lost update on the *same* page a loud 409 instead of a silent overwrite.

## API

The same routes serve **two faces**: agents get JSON/markdown by default; browsers get
styled HTML (dark GitHub-like pages: home + search box, rendered pages with resolved
wikilinks, tag chips, out/backlinks panels, a clickable force-directed SVG graph, search
with snippets, lint as a page). Decision per request: `Accept: text/html` or `?html=1`
=> HTML, everything else => raw. Point a browser at `/` and just read — no Obsidian, no
sync, works on phones.

| Route | What it gives a client |
|---|---|
| `GET /` | human home (index + search); JSON clients keep using `/index` |
| `GET /view?path=...` | human page view (wikilinks clickable, backlinks panel) |
| `GET /health` | liveness + page count |
| `GET /help` | the route table, as text (self-documenting — point new agents here) |
| `GET /index`, `GET /schema` | entry point / the conventions every writer must follow (`?format=json` wraps) |
| `GET /log?tail=30` | recent history of the wiki |
| `GET /search?q=terms&limit=10` | pages containing all terms, with titles + matching lines |
| `GET /page?path=dir/name.md` | the page's markdown (+ `X-Content-Sha256` header for optimistic writes) |
| `GET /neighbors?path=...` | `{"out": [links], "in": [backlinks]}` — the local map for graph walking |
| `GET /graph` | the whole map: nodes (path/title/tags) + edges; `?`-prefixed edges are broken |
| `GET /lint` | graph health: orphans, thin outlinks, broken edges, index gaps, frontmatter warnings |
| `POST /page` | JSON `{"path", "content", "note"?, "base"?}` — create/update one page; maintains `index.md` + `log.md` for you; returns the new `sha` for your next edit; 409 if `base` is stale |
| `POST /log` | JSON `{"note"}` — note-only entry (curator runs reporting themselves) |
| `DELETE /page?path=...&note=...` | remove a page + its index line; logged |

Paths must be under `entities/`, `concepts/`, `comparisons/` or `queries/` (plus the special `index`/`schema`/`log` reads). `POST /page` also returns schema `warnings` (missing frontmatter keys, no outbound links, type/directory mismatch) — writers get the SCHEMA.md teeth immediately, without a lint round-trip. No auth — run it LAN-internal (WireGuard reaches it from elsewhere).

## Run

```
docker run -d -p 8001:8000 -v /path/to/wiki:/wiki ghcr.io/haasanen/wiki-server:v0.2.0
```

Compose service (8001 on the host: 8000 is Strata's):

```yaml
  wiki:
    image: ghcr.io/haasanen/wiki-server:v0.2.0
    volumes:
      - /ssd/wiki-data:/wiki
    ports:
      - "8001:8000"
    restart: unless-stopped
```

Env: `WIKI_DIR` (default `/wiki`), `WIKI_PORT` (default `8000`).

## Tests

`python3 tests/test_server.py` — 63 end-to-end checks (incl. HTML face: every human route returns rendered HTML for browsers and stays raw for agents) against a live server: every route,
create/update/delete with index + log maintenance, optimistic locking (200/409 races), path
traversal guards, schema warnings, short-name link resolution with broken edges kept visible,
backlinks, lint, and concurrency (8 writers on different pages + the shared index, same-page
race exactly one winner).

## Releases

Tags `v*` build and publish `ghcr.io/haasanen/wiki-server:<tag>` (+ `latest`) via GitHub Actions. The repo holds the tool only — wiki *content* is a plain folder on a volume, never in git.
