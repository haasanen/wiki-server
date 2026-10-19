"""Human-facing HTML views for wiki-server (stdlib only).

The API (JSON) stays the default for agents; browsers get these by sending
`Accept: text/html` or `?html=1`. Routes:

  GET /                      home: index + search box
  GET /view?path=...         render one page: frontmatter chips, wikilinks,
                             out/in links with click-through
  GET /search?q=...          results with snippets
  GET /graph                 SVG map: nodes clickable, broken edges dashed red
"""
import html as H
import json
from urllib.parse import quote as urlquote
import math
import re
from pathlib import Path

WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:#([^\]|]*))?(?:\|([^\]]+))?\]\]")


def esc(s):
    return H.escape(str(s), quote=True)


# ---------- markdown -> html (small, opinionated: enough for wiki pages) ----

def md_to_html(text: str, link_map=None) -> str:
    lines = text.splitlines()
    out, i, in_code, para = [], 0, False, []

    def flush_para():
        if para:
            out.append("<p>" + inline(" ".join(para), link_map) + "</p>")
            para.clear()

    while i < len(lines):
        ln = lines[i]
        if ln.strip().startswith("```"):
            flush_para()
            if not in_code:
                out.append("<pre><code>")
                in_code = True
            else:
                out.append("</code></pre>")
                in_code = False
            i += 1
            continue
        if in_code:
            out.append(esc(ln) + "\n")
            i += 1
            continue
        s = ln.strip()
        if not s:
            flush_para()
            i += 1
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", s)
        if m:
            flush_para()
            lvl = len(m.group(1))
            out.append(f"<h{lvl}>{inline(m.group(2), link_map)}</h{lvl}>")
            i += 1
            continue
        if s in ("---", "***"):
            flush_para()
            out.append("<hr>")
            i += 1
            continue
        if s.startswith("<details") or s.startswith("</details") or s.startswith("<summary"):
            flush_para()
            out.append(esc(s).replace("&lt;", "<").replace("&gt;", ">"))
            i += 1
            continue
        if s.startswith("> "):
            flush_para()
            out.append("<blockquote>" + inline(s[2:], link_map) + "</blockquote>")
            i += 1
            continue
        if s.startswith("- ") or s.startswith("* "):
            flush_para()
            items = []
            while i < len(lines):
                t = lines[i].strip()
                if t.startswith(("- ", "* ")):
                    items.append(inline(t[2:], link_map))
                    i += 1
                elif lines[i].startswith(("    ", "\t")) and items:
                    items[-1] += " " + inline(t, link_map)   # wrapped continuation
                    i += 1
                else:
                    break
            out.append("<ul>" + "".join(f"<li>{x}</li>" for x in items) + "</ul>")
            continue
        para.append(s)
        i += 1
    flush_para()
    if in_code:
        out.append("</code></pre>")
    return "\n".join(out)


def inline(s: str, link_map=None) -> str:
    # wikilinks first (they may contain underscores/brackets)
    def wl(m):
        target, _anchor, label = m.group(1).strip(), m.group(2), m.group(3)
        label = label or target.rsplit("/", 1)[-1]
        href = link_map.get(target) if link_map else None
        cls = ' class="dead"' if href is None and link_map is not None else ""
        href = href or f"/view?q={urlquote(target)}"
        return f'<a href="{H.escape(href)}"{cls}>{H.escape(label)}</a>'
    s = WIKILINK.sub(wl, s)
    s = re.sub(r"`([^`]+)`", lambda m: f"<code>{esc(m.group(1))}</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", lambda m: f"<strong>{esc(m.group(1))}</strong>", s)
    s = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", lambda m: f"<em>{esc(m.group(1))}</em>", s)
    s = re.sub(r"~~([^~]+)~~", lambda m: f"<del>{esc(m.group(1))}</del>", s)
    s = re.sub(r"\[([ xX])\]", lambda m: (
        '<span class="box done">&#x2611;</span>' if m.group(1) != " " else '<span class="box">&#x2610;</span>'), s)
    s = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
               lambda m: f'<a href="{H.escape(m.group(2))}">{esc(m.group(1))}</a>', s)
    return s


# ---------- chrome ----------------------------------------------------------

CSS = """
:root{color-scheme:dark;--bg:#0d1117;--fg:#c9d1d9;--dim:#8b949e;--acc:#58a6ff;--line:#30363d;--chip:#21262d}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.6 -apple-system,"Segoe UI",Roboto,sans-serif}
header{display:flex;gap:14px;align-items:center;padding:10px 18px;border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--bg);z-index:2}
header a{color:var(--acc);text-decoration:none}header b{font-size:15px}
form{margin-left:auto;display:flex;gap:6px}input{background:var(--chip);border:1px solid var(--line);color:var(--fg);padding:5px 9px;border-radius:6px;min-width:220px}
main{max-width:900px;margin:0 auto;padding:22px 26px 60px}a{color:var(--acc);text-decoration:none}a:hover{text-decoration:underline}
a.dead{color:#f85149}
h1{font-size:1.7em;margin:.2em 0 .4em}h2{margin-top:1.4em;border-bottom:1px solid var(--line);padding-bottom:.2em}
code{background:var(--chip);padding:1px 5px;border-radius:4px;font:13px/1.5 ui-monospace,Consolas,monospace}
pre{background:var(--chip);padding:12px;border-radius:8px;overflow-x:auto;border:1px solid var(--line)}pre code{background:none;padding:0}
blockquote{border-left:3px solid var(--line);margin:1em 0;padding:.2em 1em;color:var(--dim)}
ul{padding-left:1.3em}li{margin:.15em 0}hr{border:none;border-top:1px solid var(--line);margin:1.6em 0}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin:.4em 0 1.2em}.chip{background:var(--chip);border:1px solid var(--line);color:var(--dim);border-radius:999px;padding:1px 10px;font-size:12px}
.side{margin-top:2.5em;border-top:1px solid var(--line);padding-top:1em}.side h3{font-size:.85em;color:var(--dim);text-transform:uppercase;letter-spacing:.05em}
.side a{display:inline-block;margin:2px 8px 2px 0;font-size:13px}
.snip{color:var(--dim);font-size:13px;margin:.3em 0 0}
.res{margin:1.1em 0}.res .path{color:var(--dim);font-size:12px}
.muted{color:var(--dim)}
.box{font-size:1.05em}.box.done{color:#3fb950}del{color:var(--dim)}svg{max-width:100%}
.node circle{cursor:pointer}.node text{fill:var(--fg);font-size:9px;pointer-events:none}
.edge{stroke:#3d444d;stroke-width:1}.edge.broken{stroke:#f85149;stroke-dasharray:3 3}
"""

def shell(title: str, body: str, q: str = "") -> str:
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} — wiki</title><style>{CSS}</style></head><body>
<header><b><a href="/">wiki</a></b>
<a href="/schema">schema</a><a href="/log">log</a><a href="/graph">graph</a><a href="/lint">lint</a><a href="/help">api</a>
<form action="/search" method="get"><input name="q" placeholder="search…" value="{esc(q)}" autofocus></form>
</header><main>{body}</main></body></html>"""


# ---------- views -----------------------------------------------------------

def page_home(core):
    idx = ""
    try:
        idx = core.read_text(core.WIKI / "index.md")
    except OSError:
        pass
    lm = _link_map(core)
    n = len(core.ok_pages())
    body = (f'<p class="muted">{n} pages · shared knowledge store · <a href="/help">machine API</a></p>'
            + _fix_links(idx, lm))
    return shell("home", body)


def _link_map(core):
    """short name -> /view?path= for every existing page (mirrors server resolution)."""
    lm = {}
    for p in core.ok_pages():
        rel = core.rel_of(p)                    # e.g. entities/strata.md
        href = f"/view?path={rel}"
        lm[Path(rel).stem] = href
        lm[rel] = href
        lm[rel[:-3]] = href
    return lm


def _wl_html(m, lm):
    target, _a, label = m.group(1).strip(), m.group(2), m.group(3)
    label = esc(label or target.rsplit("/", 1)[-1])
    href = lm.get(target)
    if href:
        return f'<a href="{href}">{label}</a>'
    return f'<a class="dead" href="/search?q={Path(target).name}">{label}</a>'


def _fix_links(md_text, lm):
    body = re.sub(r"(?s)\A---.*?---\s*", "", md_text)
    return md_to_html(body, lm)


def _frontmatter_chips(fm):
    if not fm:
        return ""
    bits = []
    for k in ("type", "updated", "confidence"):
        v = fm.get(k)
        if v:
            bits.append(f'<span class="chip">{esc(k)}: {esc(v)}</span>')
    for t in (fm.get("tags") or []):
        bits.append(f'<span class="chip">#{esc(t)}</span>')
    return '<div class="chips">' + "".join(bits) + "</div>"


def page_view(core, q):
    lm = _link_map(core)
    rel = q.get("path", [""])[0]
    target = rel or q.get("q", [""])[0]
    p = core.resolve(target)
    if p is None or not p.is_file():
        # short-name fallback: strata -> entities/strata.md (first hit)
        for cand in ("entities", "concepts", "comparisons", "queries"):
            f = core.WIKI / cand / (Path(str(target)).name + ".md")
            if f.is_file():
                p, rel = f, core.rel_of(f)
                break
    if p is None or not p.is_file():
        return shell("not found", f'<h1>no page</h1><p class="muted">{esc(target)!r} not found. '
                     f'Try <a href="/search?q={urlquote(Path(str(target)).name)}">search</a>.</p>')
    rel = rel or core.rel_of(p)
    text = core.read_text(p)
    fm = core.frontmatter(text) if hasattr(core, "frontmatter") else {}
    nb = core.neighbors(rel)
    title = (fm or {}).get("title") or Path(rel).stem
    out_h = "".join(f'<a href="/view?path={H.escape(x)}">{esc(Path(x).name)}</a>' for x in nb["out"])
    in_h = "".join(f'<a href="/view?path={H.escape(x)}">{esc(Path(x).name)}</a>' for x in nb["in"])
    for x in nb["out"]:
        if x not in lm and "/" in x:
            in_h = in_h  # broken out edges already dead-classed in body
    body = (f"<h1>{esc(title)}</h1>" + _frontmatter_chips(fm)
            + f'<p class="muted">{esc(rel)} · <a href="/page?path={urlquote(rel)}">raw</a></p>'
            + _fix_links(text, lm)
            + f'<div class="side"><h3>out links</h3><div>{out_h or "<span class=muted>none</span>"}</div>'
              f'<h3>backlinks</h3><div>{in_h or "<span class=muted>none</span>"}</div></div>')
    return shell(title, body)


def page_search(core, q):
    terms = q.get("q", [""])[0]
    if not terms:
        return shell("search", '<h1>search</h1><p class="muted">type a query above.</p>', terms)
    res = core.search(terms, limit=50)
    lm = _link_map(core)
    items = []
    for h in res:
        snips = "".join(f'<p class="snip">{esc(s[:220])}</p>' for s in h.get("hits", [])[:3])
        href = lm.get(h["path"], f'/view?path={h["path"]}')
        items.append(f'<div class="res"><a href="{href}"><b>{esc(h.get("title") or h["path"])}</b></a> '
                     f'<span class="path">{esc(h["path"])}</span>{snips}</div>')
    body = (f"<h1>{len(res)} result{'s' if len(res) != 1 else ''} for {esc(terms)!r}</h1>"
            + ("".join(items) or '<p class="muted">nothing matched — the knowledge you want may not be filed yet.</p>'))
    return shell(f'search: {terms}', body, terms)


def page_graph(core, g):
    nodes = [n for n in g["nodes"] if not n["path"].startswith("?")]
    broken = [e for e in g["edges"] if str(e[1]).startswith("?")]
    W, Hh, R = 1000, 720, 300
    pos = {}
    n = max(len(nodes), 1)
    for i, node in enumerate(nodes):
        a = 2 * math.pi * i / n
        pos[node["path"]] = (W / 2 + R * math.cos(a), Hh / 2 + R * 0.8 * math.sin(a))
    # 120 spring iterations
    idx_by = {nd["path"]: i for i, nd in enumerate(nodes)}
    for _ in range(120):
        disp = {p: [0.0, 0.0] for p in pos}
        keys = list(pos)
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                a, b = keys[i], keys[j]
                dx, dy = pos[a][0] - pos[b][0], pos[a][1] - pos[b][1]
                d2 = max(dx * dx + dy * dy, 40)
                f = 2600.0 / d2
                disp[a][0] += dx * f; disp[a][1] += dy * f
                disp[b][0] -= dx * f; disp[b][1] -= dy * f
        for f_, t_ in g["edges"]:
            if f_ in pos and t_ in pos:
                dx, dy = pos[f_][0] - pos[t_][0], pos[f_][1] - pos[t_][1]
                d = math.hypot(dx, dy) or 1
                f = (d - 130) * 0.004
                disp[f_][0] -= dx / d * f * d; disp[f_][1] -= dy / d * f * d
                disp[t_][0] += dx / d * f * d; disp[t_][1] += dy / d * f * d
        for p in pos:
            x, y = pos[p][0] + disp[p][0], pos[p][1] + disp[p][1]
            pos[p] = (min(max(x, 40), W - 40), min(max(y, 40), Hh - 30))
    lines, circles = [], []
    for f_, t_ in g["edges"]:
        if f_ in pos and t_ in pos:
            cls = "edge"
            lines.append(f'<line class="{cls}" x1="{pos[f_][0]:.0f}" y1="{pos[f_][1]:.0f}" x2="{pos[t_][0]:.0f}" y2="{pos[t_][1]:.0f}"/>')
    for nd in nodes:
        x, y = pos[nd["path"]]
        colour = "#58a6ff" if nd["path"].startswith("entities/") else ("#d29922" if nd["path"].startswith("concepts/") else ("#8b949e" if nd["path"].startswith("queries/") else "#3fb950"))
        circles.append(f'<g class="node"><circle cx="{x:.0f}" cy="{y:.0f}" r="6" fill="{colour}"><title>{esc(nd["path"])}{esc((nd.get("title") or ""))}</title></circle>'
                       f'<text x="{x + 8:.0f}" y="{y + 3:.0f}"><a href="/view?path={urlquote(nd["path"])}"><tspan>{esc(Path(nd["path"]).stem)}</tspan></a></text></g>')
    svg = f'<svg viewBox="0 0 {W} {Hh}" xmlns="http://www.w3.org/2000/svg">{"".join(lines)}{"".join(circles)}</svg>'
    body = (f'<h1>graph</h1><p class="muted">{len(nodes)} nodes · {len(g["edges"])} edges · '
            f'blue entity · yellow concept · grey raw-note · green other{f" · {len(broken)} broken edges (dashed red)" if broken else ""}</p>' + svg)
    return shell("graph", body)


def page_md(core, text: str, title: str) -> str:
    return shell(title, f"<h1>{esc(title)}</h1>" + _fix_links(text, _link_map(core)))


def page_lint(core, d: dict) -> str:
    def lst(items, note=""):
        return ("<ul>" + "".join(f"<li>{esc(i)}</li>" for i in items) + "</ul>") if items else f'<p class="muted">none{note}</p>'
    fm = d.get("frontmatter_warnings") or {}
    fml = "<ul>" + "".join(f"<li><code>{esc(k)}</code>: {esc('; '.join(v) if isinstance(v, list) else v)}</li>" for k, v in fm.items()) + "</ul>" if fm else '<p class="muted">none</p>'
    body = (f'<h1>lint</h1><p class="muted">{d["pages"]} pages</p>'
            + f"<h2>orphans</h2>{lst(d['orphans'])}<h2>thin outlinks</h2>{lst(d['thin_outlinks'])}"
            + f"<h2>broken edges</h2>{lst(d['broken_edges'])}<h2>missing from index</h2>{lst(d['missing_from_index'])}"
            + f"<h2>frontmatter warnings</h2>{fml}")
    return shell("lint", body)
