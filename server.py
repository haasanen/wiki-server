#!/usr/bin/env python3
"""A tiny HTTP server for a Karpathy-pattern knowledge wiki: interlinked markdown on disk.

The graph is the files: pages link with [[wikilinks]], this server gives LAN clients
search / read / neighbors / upsert without ever shipping the whole corpus. No auth
(LAN-internal service; put it behind WireGuard to reach it from elsewhere).

    GET  /health                 -> {"status": "ok", "pages": N}
    GET  /index                  -> index.md as text (?format=json wraps it)
    GET  /schema                 -> SCHEMA.md as text (the conventions every writer follows)
    GET  /search?q=terms         -> [{"path","title","summary","hits":[lines]}] pages containing ALL terms
    GET  /page?path=X            -> the page's markdown (200 text) or 404
    GET  /neighbors?path=X       -> {"out": [links], "in": [pages linking here]}
    POST /page  {"path","content","note"?}
                                 -> create or update a page; appends note to log.md;
                                    adds the page to index.md if it is missing. 200 on
                                    create or update, with {"created": bool}.

Writes are per-page files, atomic (tmp + rename), which is the whole concurrency story:
two machines editing different concepts cannot collide; the same page is last-write-wins
(both agents run on one model's judgment, not three-way text merges).
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

WIKI = Path(os.environ.get("WIKI_DIR", "/wiki"))
PAGE_DIRS = ("entities", "concepts", "comparisons", "queries")      # graph nodes
SPECIAL = ("SCHEMA.md", "index.md", "log.md")
WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]")
LOCK = threading.Lock()                                            # only to make index/log appends safe


def ok_pages() -> list[Path]:
    out = []
    for d in PAGE_DIRS:
        p = WIKI / d
        if p.is_dir():
            out += sorted(p.rglob("*.md"))
    return out


def read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def title_of(text: str, fallback: str) -> str:
    m = re.search(r"(?m)^#\s+(.+)$", text)
    if m:
        return m.group(1).strip()
    m = re.search(r"(?m)^title:\s*(.+)$", text)
    return m.group(1).strip() if m else fallback


def resolve(rel: str) -> Path | None:
    """A page path inside the wiki (no traversal), or None if not allowed."""
    rel = rel.strip().lstrip("/")
    if rel.endswith(".md") and rel in SPECIAL:
        return WIKI / rel
    if not rel.endswith(".md"):
        rel += ".md"
    d, _, name = rel.partition("/")
    if d in PAGE_DIRS and name and ".." not in Path(rel).parts:
        return WIKI / rel
    return None


def index_summary_line(rel: str, text: str) -> str:
    """One index line for a page: [[rel]] — first body paragraph's first sentence."""
    title = title_of(text, Path(rel).stem)
    body = re.sub(r"(?s)\A---.*?---\s*", "", text)                       # drop frontmatter
    body = re.sub(r"(?m)^#.*$", "", body).strip()
    first = re.split(r"(?<!\w)[.!?]\s|\n\s*\n", body.replace("\n", " ").strip(), maxsplit=1)[0]
    first = re.sub(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]", r"[[\1]]", first)[:160]
    return f"- [[{rel.rsplit('.', 1)[0]}]] — {title}: {first}"


def section_of(rel: str) -> str:
    return {"entities": "Entities", "concepts": "Concepts",
            "comparisons": "Comparisons", "queries": "Queries"}[rel.split("/", 1)[0]]


def ensure_in_index(rel: str, text: str) -> None:
    idx = WIKI / "index.md"
    try:
        cur = read_text(idx)
    except OSError:
        cur = "# Wiki Index\n"
    stem = rel.rsplit(".", 1)[0]
    if f"[[{stem}]]" in cur:
        return
    line = index_summary_line(rel, text)
    header = f"## {section_of(rel)}"
    lines = cur.splitlines()
    at = next((i for i, l in enumerate(lines) if l.strip() == header), None)
    if at is None:
        lines += ["", header, line]
    else:
        end = next((i for i in range(at + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
        while end > at + 1 and not lines[end - 1].strip():
            end -= 1
        lines.insert(end, line)
    tmp = idx.with_suffix(".tmp")
    tmp.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    os.replace(tmp, idx)


def neighbors(rel: str) -> dict:
    stem = rel.rsplit(".", 1)[0]
    base = Path(stem).name                              # pages link each other by short name: [[strata]]
    out, ins, seen = [], [], set()
    own = resolve(rel)
    if own and own.is_file():
        for m in WIKILINK.finditer(read_text(own)):
            t = m.group(1).strip()
            if t != base and t not in seen:
                seen.add(t)
                # resolve the short name to a real page if one exists, else keep as-is (a broken edge is visible)
                hit = next((str(p.relative_to(WIKI)).rsplit(".", 1)[0] for p in ok_pages()
                            if p.stem == t), t)
                out.append(hit)
    for p in ok_pages():
        r = str(p.relative_to(WIKI))
        if r == rel:
            continue
        for m in WIKILINK.finditer(read_text(p)):
            if m.group(1).strip() in (base, stem, r.rsplit(".", 1)[0]):
                ins.append(r.rsplit(".", 1)[0])
                break
    return {"out": out, "in": sorted(ins)}


def search(q: str) -> list[dict]:
    terms = [t.lower() for t in re.split(r"\W+", q) if t]
    hits = []
    for p in ok_pages():
        text = read_text(p)
        low = text.lower()
        if terms and not all(t in low for t in terms):
            continue
        lines = [l.strip() for l in text.splitlines() if all(t in l.lower() for t in terms)]
        rel = str(p.relative_to(WIKI))
        hits.append({"path": rel, "title": title_of(text, p.stem),
                     "summary": lines[0][:200] if lines else "", "hits": lines[:3]})
    return hits


def atomic_write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


class H(BaseHTTPRequestHandler):
    def log_message(self, fmt, *a):                       # quiet: one line per request
        sys.stderr.write(f"{time.strftime('%H:%M:%S')} {self.address_string()} {fmt % a}\n")

    def _send(self, code, body, ctype="application/json"):
        data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/health":
            return self._send(200, {"status": "ok", "pages": len(ok_pages()), "service": "wiki"})
        if u.path in ("/index", "/schema"):
            f = WIKI / (u.path.strip("/") + ".md")
            if not f.is_file():
                return self._send(404, {"error": f"no {u.path.strip('/').lower()}"})
            if q.get("format", [""])[0] == "json":
                return self._send(200, {"content": read_text(f)})
            return self._send(200, read_text(f), "text/markdown")
        if u.path == "/search":
            if not q.get("q"):
                return self._send(400, {"error": "q=terms required"})
            return self._send(200, search(q["q"][0]))
        if u.path in ("/page", "/neighbors"):
            rel = q.get("path", [""])[0]
            p = resolve(rel)
            if p is None:
                return self._send(400, {"error": f"bad path {rel!r}; pages live under {', '.join(PAGE_DIRS)}/"})
            if u.path == "/neighbors":
                return self._send(200, neighbors(rel))
            if not p.is_file():
                return self._send(404, {"error": f"no page {rel}", "did_you_mean": [
                    h["path"] for h in search(Path(rel).stem)][:3]})
            return self._send(200, read_text(p), "text/markdown")
        return self._send(404, {"error": "routes: /health /index /schema /search /page /neighbors"})

    def do_POST(self):
        if urlparse(self.path).path != "/page":
            return self._send(404, {"error": "POST is /page only"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or b"{}"))
            rel, content = body["path"], body["content"]
        except (ValueError, KeyError) as e:
            return self._send(400, {"error": f"need JSON {{path, content}} ({e})"})
        p = resolve(rel)
        if p is None:
            return self._send(400, {"error": f"bad path {rel!r}; pages live under {', '.join(PAGE_DIRS)}/"})
        if "\n" not in content or not content.strip():
            return self._send(400, {"error": "content looks empty"})
        rel = str(p.relative_to(WIKI))
        with LOCK:
            created = not p.is_file()
            atomic_write(p, content.rstrip() + "\n")
            ensure_in_index(rel, content)
            if body.get("note"):
                log = WIKI / "log.md"
                entry = f"\n## [{time.strftime('%Y-%m-%d')}] {'create' if created else 'update'} | {rel}\n- {body['note']} (via api)\n"
                with open(log, "a", encoding="utf-8") as f:
                    f.write(entry)
        return self._send(200, {"path": rel, "created": created})


def main() -> int:
    port = int(os.environ.get("WIKI_PORT", "8000"))
    if not WIKI.is_dir():
        sys.stderr.write(f"wiki dir {WIKI} missing\n")
        return 1
    srv = ThreadingHTTPServer(("0.0.0.0", port), H)
    sys.stderr.write(f"wiki server: {WIKI} ({len(ok_pages())} pages) on :{port}\n")
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
