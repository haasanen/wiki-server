#!/usr/bin/env python3
"""A small LAN HTTP server for a Karpathy-pattern knowledge wiki: interlinked markdown on disk.

The graph is the files: pages link with [[wikilinks]]; this server lets every machine on the
network read, search, walk links, and edit pages without keeping local copies. Stdlib only,
no database, no auth (run it LAN-internal; reach it from elsewhere over WireGuard).

  GET  /health                      {"status","pages","version"}
  GET  /help                        the route table, as text (self-documenting)
  GET  /index   ?format=json        index.md (coarse entry point; navigate by links)
  GET  /schema  ?format=json        SCHEMA.md - the conventions every writer must follow
  GET  /log     ?tail=30            the last N log entries (default: whole file)
  GET  /search  ?q=terms&limit=10   pages containing ALL terms, with titles + matching lines
  GET  /page    ?path=dir/name.md   the page's markdown (+ X-Content-Sha256 header)
  GET  /neighbors ?path=...         {"out":[links],"in":[backlinks]} - the local map for walking
  GET  /graph                       the whole map: {"nodes":[...],"edges":[from,to]} (titles/tags only)
  GET  /lint                        graph health: orphans, broken edges, index gaps, frontmatter
  POST /page    {"path","content","note"?,"base"?}
                                    create/update one page; "base" = the sha you read (optimistic
                                    locking -> 409 if it changed); appends "note" to the log and
                                    adds/refreshes the page's index.md line; returns the new sha
  POST /log     {"note"}            append a note-only entry (a curator run reporting itself)
  DELETE /page  ?path=...&note=...  delete one page, remove its index line, log it

Paths for pages: entities/, concepts/, comparisons/, queries/ (plus the special index/schema/log
reads). Writes are per-page atomic (tmp + rename) under a per-path lock: two clients editing
different pages cannot collide; the same page is last-write-wins unless "base" is given, which
makes a lost update a loud 409 instead of a silent overwrite.

Environment: WIKI_DIR (default /wiki), WIKI_PORT (default 8000).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

VERSION = "0.4.3"
WIKI = Path(os.environ.get("WIKI_DIR", "/wiki"))
PAGE_DIRS = ("entities", "concepts", "comparisons", "queries")      # graph node dirs
try:
    import views as V
except ImportError:
    V = None

SPECIAL = ("SCHEMA.md", "index.md", "log.md")
TYPES = {"entities": "entity", "concepts": "concept", "comparisons": "comparison", "queries": "query"}
SECTION = {"entities": "Entities", "concepts": "Concepts", "comparisons": "Comparisons", "queries": "Queries"}
REQUIRED_FM = ("title", "created", "updated", "type", "tags")
WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]")
MAX_BODY = 2 * 1024 * 1024                                          # 2 MB per page: plenty, caps abuse

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def pathlock(key: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


# --------------------------------------------------------------------------- reading

def ok_pages() -> list[Path]:
    out = []
    for d in PAGE_DIRS:
        p = WIKI / d
        if p.is_dir():
            out += sorted(p.rglob("*.md"))
    return out


def read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def frontmatter(text: str) -> dict:
    """The YAML frontmatter as flat key -> string (enough for our schema; no yaml import)."""
    m = re.match(r"(?s)\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", text)
    if not m:
        return {}
    fm, key = {}, None
    for line in m.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        mm = re.match(r"([A-Za-z0-9_]+):\s*(.*)$", line)
        if mm:
            key = mm.group(1)
            fm[key] = mm.group(2).strip()
        elif key and (line.startswith(" ") or line.startswith("-")):
            fm[key] += " " + line.strip()                           # multi-line list continuation
    return fm


def title_of(text: str, fallback: str) -> str:
    m = re.search(r"(?m)^#\s+(.+)$", text)
    if m:
        return m.group(1).strip()
    return frontmatter(text).get("title") or fallback


def body_of(text: str) -> str:
    return re.sub(r"(?s)\A---.*?---\s*", "", text)


def resolve(rel: str) -> Path | None:
    """A page path inside the wiki (no traversal), or None if not an allowed shape."""
    rel = unquote(rel).strip().lstrip("/")
    if rel.endswith(".md") and rel in SPECIAL:
        return WIKI / rel
    if not rel.endswith(".md"):
        rel += ".md"
    d, _, name = rel.partition("/")
    if d in PAGE_DIRS and name and ".." not in Path(rel).parts:
        return WIKI / rel
    return None


def rel_of(p: Path) -> str:
    return str(p.relative_to(WIKI)).replace(os.sep, "/")


def sha_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


def pages_path(rel: str) -> Path:
    return WIKI / (rel if rel.endswith(".md") else rel + ".md")


# --------------------------------------------------------------------------- the graph

def edges_from(text: str, own_stem: str) -> list[str]:
    out, seen = [], set()
    for m in WIKILINK.finditer(text):
        t = m.group(1).strip()
        if t and t != own_stem and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def resolve_target(t: str, page_paths: set[str]) -> str | None:
    """A [[short name]] to a real page path (short names are unique by convention), else None."""
    if t.endswith(".md") and t in page_paths:
        return t
    hits = [p for p in page_paths if Path(p).stem == t or Path(p).name == t]
    return hits[0] if len(hits) == 1 else None


def neighbors(rel: str) -> dict:
    stem = rel.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    page_paths = {rel_of(p) for p in ok_pages()}
    own = resolve(rel)
    out, ins = [], []
    if own and own.is_file():
        for t in edges_from(read_text(own), stem):
            hit = resolve_target(t, page_paths)
            out.append(hit.rsplit(".", 1)[0] if hit else t)         # broken edges stay visible
    for p in sorted(page_paths):
        pstem = Path(p).stem
        if pstem == stem:
            continue
        for t in edges_from(read_text(WIKI / p), pstem):
            hit = resolve_target(t, page_paths)
            if (hit and Path(hit).stem == stem) or (hit is None and t == stem):
                ins.append(p.rsplit(".", 1)[0])
                break
    return {"out": out, "in": sorted(set(ins))}


def graph() -> dict:
    nodes, edges = [], set()
    page_paths = {rel_of(p) for p in ok_pages()}
    for rel in sorted(page_paths):
        stem = Path(rel).stem
        text = read_text(WIKI / rel)
        tags = [t.strip() for t in re.split(r"[,\[\]]", frontmatter(text).get("tags", "")) if t.strip()]
        nodes.append({"path": rel.rsplit(".", 1)[0], "title": title_of(text, stem), "tags": tags})
        for t in edges_from(text, stem):
            hit = resolve_target(t, page_paths)
            edges.add((rel.rsplit(".", 1)[0], hit.rsplit(".", 1)[0]) if hit else (rel.rsplit(".", 1)[0], "?" + t))
    return {"nodes": nodes, "edges": sorted(edges)}


# --------------------------------------------------------------------------- search

def search(q: str, limit: int = 10) -> list[dict]:
    terms = [t.lower() for t in re.split(r"\W+", q) if t]
    hits = []
    for p in ok_pages():
        text = read_text(p)
        low = text.lower()
        if terms and not all(t in low for t in terms):
            continue
        lines = [l.strip() for l in text.splitlines() if all(t in l.lower() for t in terms)]
        hits.append({"path": rel_of(p), "title": title_of(text, p.stem),
                     "summary": lines[0][:200] if lines else first_line(text), "hits": lines[:3]})
        if len(hits) >= limit:
            break
    return hits


def first_line(text: str) -> str:
    for l in body_of(text).splitlines():
        if l.strip() and not l.startswith("#"):
            return l.strip()[:200]
    return ""


# --------------------------------------------------------------------------- writing

def atomic_write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content.rstrip() + "\n")
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def index_line_for(rel: str, text: str) -> str:
    stem = Path(rel).stem                       # index uses short names: [[strata]], matching the wiki convention
    title = title_of(text, Path(rel).stem)
    body = re.sub(r"(?m)^#.*$", "", body_of(text)).strip()
    first = re.split(r"(?<!\w)[.!?]\s|\n\s*\n", body.replace("\n", " ").strip(), maxsplit=1)[0]
    if first.lower().startswith(title.lower()):          # pages that repeat their own title: don't double it
        first = first[len(title):].lstrip(" :.\u2014-")
    first = first.replace("**", "")                      # index lines are one-liners: bold there renders raw
    if len(first) > 160:
        cut = first[:160]
        if " " in cut:                                   # word boundary, never mid-link or mid-word
            cut = cut[:cut.rfind(" ")]
        first = cut.rstrip(" ,;:)-(" + chr(8212) + "\u2014[")
        if first.count("[[") > first.count("]]"):        # never leave a half [[link]]
            first = first[:first.rfind("[[")].rstrip(" ,;:)-(" + chr(8212) + " [")
    return f"- [[{stem}]] — {first}" if first else f"- [[{stem}]] — {title}"


def update_index(rel: str, text: str, *, remove: bool = False) -> None:
    """Add or refresh the page's line in index.md (or remove it). Locked: the index is the one
    shared file, so every index edit runs under its lock."""
    with pathlock("index.md"):
        if rel in SPECIAL:
            return          # index.md / log.md / SCHEMA.md don't get an index line of their own
        idx = WIKI / "index.md"
        try:
            cur = read_text(idx)
        except OSError:
            cur = "# Wiki Index\n"
        stem = Path(rel).stem
        lines = cur.splitlines()
        matches = [i for i, l in enumerate(lines) if l.strip().startswith(f"- [[{stem}]]")]
        at = matches[0] if matches else None
        if remove:
            for i in reversed(matches):
                del lines[i]
            if matches:
                atomic_write(idx, "\n".join(lines).rstrip() + "\n")
            return
        for i in reversed(matches[1:]):
            del lines[i]                 # self-heal historical duplicate lines
        new = index_line_for(rel, text)
        if at is None:
            header = f"## {SECTION[rel.split('/', 1)[0]]}"
            h = next((i for i, l in enumerate(lines) if l.strip() == header), None)
            if h is None:
                lines += ["", header, new]
            else:
                end = next((i for i in range(h + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
                while end > h + 1 and not lines[end - 1].strip():
                    end -= 1
                lines.insert(end, new)
        elif lines[at] != new:
            lines[at] = new                                         # refresh the summary on update
        atomic_write(idx, "\n".join(lines).rstrip() + "\n")


def append_log(entry: str) -> None:
    with pathlock("log.md"):
        log = WIKI / "log.md"
        if not log.is_file():
            atomic_write(log, "# Wiki Log\n\n> Append-only. `## [YYYY-MM-DD] action | subject`\n")
        with open(log, "a", encoding="utf-8") as f:
            f.write("\n" + entry.rstrip() + "\n")


def validate_content(rel: str, content: str) -> list[str]:
    """Warnings (not refusals) about a page's content: the schema's teeth, served to writers."""
    warn = []
    if rel in SPECIAL:
        return warn          # index/log/SCHEMA have their own shapes; frontmatter rules don't apply
    fm = frontmatter(content)
    if not fm:
        warn.append("no YAML frontmatter (SCHEMA.md requires title/created/updated/type/tags)")
    else:
        for k in REQUIRED_FM:
            if not fm.get(k):
                warn.append(f"frontmatter missing '{k}'")
        want = TYPES.get(rel.split("/", 1)[0])
        if want and fm.get("type") and fm["type"] != want:
            warn.append(f"type '{fm['type']}' does not match directory ({want})")
    if not WIKILINK.search(body_of(content)):
        warn.append("no [[wikilinks]] — an unlinked page is an invisible page (SCHEMA.md: min 2 outbound)")
    return warn


# --------------------------------------------------------------------------- lint

def lint() -> dict:
    page_paths = {rel_of(p) for p in ok_pages()}
    texts = {r: read_text(WIKI / r) for r in page_paths}
    inbound: dict[str, set] = {r: set() for r in page_paths}
    broken, edges = [], {}
    for r, text in texts.items():
        outs = set()
        for t in edges_from(text, Path(r).stem):
            hit = resolve_target(t, page_paths)
            if hit:
                outs.add(hit)
                inbound[hit].add(r)
            else:
                broken.append({"from": r, "link": t})
        edges[r] = outs
    idx = read_text(WIKI / "index.md") if (WIKI / "index.md").is_file() else ""
    idx_targets = {Path(p).stem for p in re.findall(r"\[\[([^\]|#]+)", idx)}   # index may use short names
    orphans = sorted(r for r in page_paths if not inbound[r])
    thin = sorted(r for r, o in edges.items() if len(o) < 2)
    not_indexed = sorted(r for r in page_paths if Path(r).stem not in idx_targets)
    fm_warn = {r: w for r, w in ((r, validate_content(r, texts[r])) for r in sorted(page_paths)) if w}
    return {"pages": len(page_paths), "orphans": orphans, "thin_outlinks": thin,
            "broken_edges": broken, "missing_from_index": not_indexed, "frontmatter_warnings": fm_warn}


# --------------------------------------------------------------------------- HTTP

HELP = """wiki-server __VERSION__ — a Karpathy-pattern knowledge wiki over HTTP (no auth, LAN-internal)

  GET  /  /view?path=             human HTML UI (also: Accept: text/html on most GETs)
  GET  /health                    liveness + page count
  GET  /index | /schema           entry point / the conventions you must follow (add format=json to wrap)
  GET  /log?tail=30               recent history of the wiki
  GET  /search?q=terms&limit=10   all-terms search; returns path, title, matching lines
  GET  /page?path=dir/name.md     read a page (X-Content-Sha256 header for optimistic writes)
  GET  /neighbors?path=...        {"out": links, "in": backlinks} - walk the graph from here
  GET  /graph                     nodes (path/title/tags) + edges; '?'-prefixed edges are broken
  GET  /lint                      orphans, thin outlinks, broken edges, index gaps, frontmatter
  POST /page {"path","content","note"?,"base"?}
             create/update (base = the sha you read; 409 if someone else wrote first);
             maintains index.md + log.md for you; returns {"sha": ...} for your next edit
  POST /log  {"note": "..."}      append a note-only entry (curator runs reporting themselves)
  DELETE /page?path=...&note=...  remove a page and its index line; logged

Paths: entities/ concepts/ comparisons/ queries/ *.md. Convention: link pages by short name [[name]].
""".replace("__VERSION__", VERSION)


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *a):
        sys.stderr.write(f"{time.strftime('%H:%M:%S')} {self.address_string()} {fmt % a}\n")

    def _send(self, code, body, ctype="application/json", extra_headers=None):
        data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        if ctype == "text/html":
            # the .md files are the ground truth; HTML is rendered on demand, so browsers
            # must revalidate rather than trust heuristic caching (no stale views)
            self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(data)))
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def wants_html(self, q):
        if q.get("html"):
            return True
        if V is None:
            return False
        accept = self.headers.get("Accept", "")
        return "text/html" in accept and "application/json" not in accept

    # ---- GET
    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            html = self.wants_html(q)
            if u.path == "/" and V is not None:
                return self._send(200, V.page_home(sys.modules[__name__]), "text/html")
            if u.path == "/view" and V is not None:
                return self._send(200, V.page_view(sys.modules[__name__], q), "text/html")
            if u.path == "/health":
                return self._send(200, {"status": "ok", "pages": len(ok_pages()), "service": "wiki", "version": VERSION})
            if u.path == "/help":
                return self._send(200, HELP, "text/plain")
            if u.path in ("/index", "/schema"):
                f = WIKI / {"index": "index.md", "schema": "SCHEMA.md"}[u.path.strip("/")]
                if not f.is_file():
                    return self._send(404, {"error": f"no {u.path.strip('/').lower()}"})
                if q.get("format", [""])[0] == "json":
                    return self._send(200, {"content": read_text(f)})
                if html and V is not None:
                    return self._send(200, V.page_md(sys.modules[__name__], read_text(f), u.path.strip("/").title()), "text/html")
                return self._send(200, read_text(f), "text/markdown")
            if u.path == "/log":
                f = WIKI / "log.md"
                if not f.is_file():
                    return self._send(404, {"error": "no log"})
                text = read_text(f)
                tail = q.get("tail", [""])[0]
                if tail.isdigit():
                    parts = re.split(r"(?m)(?=^## )", text)
                    text = "".join(parts[:1] + parts[-int(tail):])
                if html and V is not None:
                    return self._send(200, V.page_md(sys.modules[__name__], text, "log"), "text/html")
                return self._send(200, text, "text/markdown")
            if u.path == "/search":
                if not q.get("q"):
                    return self._send(400, {"error": "q=terms required"})
                try:
                    lim = int(q.get("limit", ["10"])[0] or 10)
                except ValueError:
                    lim = 10
                res = search(q["q"][0], limit=max(1, min(lim, 100)))
                if html and V is not None:
                    return self._send(200, V.page_search(sys.modules[__name__], q), "text/html")
                return self._send(200, res)
            if u.path == "/graph":
                g = graph()
                if html and V is not None:
                    return self._send(200, V.page_graph(sys.modules[__name__], g), "text/html")
                return self._send(200, g)
            if u.path == "/lint":
                if html and V is not None:
                    return self._send(200, V.page_lint(sys.modules[__name__], lint()), "text/html")
                return self._send(200, lint())
            if u.path in ("/page", "/neighbors"):
                rel = q.get("path", [""])[0]
                p = resolve(rel)
                if p is None:
                    return self._send(400, {"error": f"bad path {rel!r}; pages live under {'/'.join(PAGE_DIRS)}/"})
                if u.path == "/neighbors":
                    return self._send(200, neighbors(rel))
                if not p.is_file():
                    return self._send(404, {"error": f"no page {rel}", "did_you_mean": [
                        h["path"] for h in search(Path(rel).stem, limit=3)]})
                text = read_text(p)
                return self._send(200, text, "text/markdown", {"X-Content-Sha256": sha_of(text)})
            return self._send(404, {"error": "see /help for routes"})
        except Exception as e:                                        # never kill the worker thread
            return self._send(500, {"error": f"{type(e).__name__}: {e}"})

    # ---- POST
    def do_POST(self):
        u = urlparse(self.path)
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._send(400, {"error": "bad Content-Length"})
        if n > MAX_BODY:
            return self._send(413, {"error": "body too large"})
        try:
            body = json.loads(self.rfile.read(n) if n else b"{}")
        except ValueError as e:
            return self._send(400, {"error": f"invalid JSON: {e}"})
        try:
            if u.path == "/log":                                      # note-only writers (curator runs)
                note = str(body.get("note", "")).strip()
                if not note:
                    return self._send(400, {"error": "note required"})
                append_log(f"## [{time.strftime('%Y-%m-%d')}] note | {note}")
                return self._send(200, {"logged": True})
            if u.path != "/page":
                return self._send(404, {"error": "POST is /page and /log only"})
            rel, content = str(body.get("path", "")), str(body.get("content", ""))
            if not content.strip() or "\n" not in content:
                return self._send(400, {"error": "content is empty or one line: refusing to clobber a page"})
            p = resolve(rel)
            if p is None:
                return self._send(400, {"error": f"bad path {rel!r}; pages live under {'/'.join(PAGE_DIRS)}/"})
            rel = rel_of(p)
            warnings = validate_content(rel, content)
            with pathlock(rel):
                existed = p.is_file()
                if existed and body.get("base"):
                    cur = sha_of(read_text(p))
                    if cur != body["base"]:
                        return self._send(409, {"error": f"page changed since you read it (now {cur}); "
                                                         "re-read, merge, write with the new base"})
                atomic_write(p, content)
            update_index(rel, content)
            note = str(body.get("note") or "").strip()
            append_log(f"## [{time.strftime('%Y-%m-%d')}] {'create' if not existed else 'update'} | {rel}"
                       + (f"\n- {note} (via api)" if note else ""))
            return self._send(200, {"path": rel, "created": not existed, "sha": sha_of(content),
                                    **({"warnings": warnings} if warnings else {})})
        except Exception as e:
            return self._send(500, {"error": f"{type(e).__name__}: {e}"})

    # ---- DELETE
    def do_DELETE(self):
        u = urlparse(self.path)
        try:
            rel = parse_qs(u.query).get("path", [""])[0]
            p = resolve(rel)
            if p is None:
                return self._send(400, {"error": f"bad path {rel!r}; pages live under {'/'.join(PAGE_DIRS)}/"})
            rel = rel_of(p)
            with pathlock(rel):
                if not p.is_file():
                    return self._send(404, {"error": f"no page {rel}"})
                p.unlink()
            update_index(rel, "", remove=True)
            note = parse_qs(u.query).get("note", [""])[0].strip()
            append_log(f"## [{time.strftime('%Y-%m-%d')}] delete | {rel}"
                       + (f"\n- {note} (via api)" if note else ""))
            return self._send(200, {"deleted": rel})
        except Exception as e:
            return self._send(500, {"error": f"{type(e).__name__}: {e}"})


def main() -> int:
    port = int(os.environ.get("WIKI_PORT", "8000"))
    if not WIKI.is_dir():
        sys.stderr.write(f"wiki dir {WIKI} missing\n")
        return 1
    srv = ThreadingHTTPServer(("0.0.0.0", port), H)
    sys.stderr.write(f"wiki-server {VERSION}: {WIKI} ({len(ok_pages())} pages) on :{port}\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
