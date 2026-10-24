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


# ---------- markdown -> html (complete coverage: CommonMark subset + wikilinks) ----
# Supported: ATX + setext headings, ordered/unordered/nested lists (loose+tight,
# start=), fenced code with language, tables with alignment, blockquotes (nested),
# thematic breaks (--- *** ___), images (lazy-load), markdown links (with title),
# wikilinks (resolved against the live page map; dead ones red), autolinks <url>,
# bare-URL linking, ***bold-italic***, **bold**, __bold__, *em*, _em_, ~~strike~~,
# task checkboxes, inline code (multi-backtick), hard breaks (two-space),
# backslash escapes, <details>/<summary>/<img> passthrough.

ATX = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
HR = re.compile(r"^([-*_])( *\1){2,} *$")
LISTITEM = re.compile(r"^(\s*)([-*+]|\d+[.)])(\s+)(.*)$")


def md_to_html(text: str, link_map=None) -> str:
    text = re.sub(r"(?s)\A---.*?---\s*", "", text)      # strip frontmatter
    return _blocks(text.splitlines(), link_map)


def _blocks(lines, lm):
    out, i, para = [], 0, []

    def flush():
        nonlocal para
        if para:
            html = ""
            for seg in para:
                if seg.endswith("\x01"):
                    html += inline(seg[:-1], lm) + "<br>"
                else:
                    if html and not html.endswith("<br>"):
                        html += " "
                    html += inline(seg, lm)
            out.append("<p>" + html + "</p>")
            para = []

    n = len(lines)
    while i < n:
        ln = lines[i]
        s = ln.strip()
        # fenced code (``` or ~~~), optional language, unclosed runs to EOF
        if s.startswith("```") or s.startswith("~~~"):
            flush()
            fence = s[:3]
            lang = s[3:].strip()
            buf = []
            i += 1
            while i < n and not lines[i].strip().startswith(fence):
                buf.append(esc(lines[i]) + "\n")
                i += 1
            i += 1
            cls = ' class="lang-' + esc(lang) + '"' if lang else ""
            out.append("<pre><code%s>%s</code></pre>" % (cls, "".join(buf)))
            continue
        # setext heading: previous paragraph + ==== / ---- underline
        if para and re.match(r"^=+ *$", s):
            txt = " ".join(para); para = []
            out.append("<h1>" + inline(txt, lm) + "</h1>"); i += 1; continue
        if para and set(s) == {"-"}:      # any run of dashes under a para = setext h2
            txt = " ".join(para); para = []
            out.append("<h2>" + inline(txt, lm) + "</h2>"); i += 1; continue
        m = ATX.match(s)
        if m:
            flush()
            lvl = len(m.group(1))
            out.append("<h%d>%s</h%d>" % (lvl, inline(m.group(2), lm), lvl))
            i += 1
            continue
        if HR.match(s):
            flush(); out.append("<hr>"); i += 1; continue
        if s.startswith(">"):
            flush()
            buf = []
            while i < n and lines[i].strip().startswith(">"):
                buf.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            out.append("<blockquote>" + _blocks(buf, lm) + "</blockquote>")
            continue
        # table: header row + |---| separator (with optional :align:), body rows
        if s.startswith("|") and s.count("|") >= 2 and i + 1 < n and "-" in lines[i + 1] \
                and re.match(r"^\s*\|?[\s:|-]+\|?\s*$", lines[i + 1]):
            flush()
            header = [c.strip() for c in s.strip().strip("|").split("|")]
            aligns = [c.strip() for c in lines[i + 1].strip().strip("|").split("|")]

            def a(col, aligns=aligns):
                c2 = aligns[col] if col < len(aligns) else ""
                if c2.startswith(":") and c2.endswith(":"):
                    return ' style="text-align:center"'
                if c2.endswith(":"):
                    return ' style="text-align:right"'
                return ""
            i += 2
            rows = []
            while i < n and lines[i].strip().startswith("|") and lines[i].count("|") >= 2:
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            th = "".join("<th%s>%s</th>" % (a(c), inline(c2, lm)) for c, c2 in enumerate(header))
            tb = "".join("<tr>" + "".join("<td%s>%s</td>" % (a(ci), inline(c2, lm)) for ci, c2 in enumerate(r)) + "</tr>" for r in rows)
            out.append('<div class="tscroll"><table><thead><tr>' + th + "</tr></thead><tbody>" + tb + "</tbody></table></div>")
            continue
        # list block: gather contiguous items + their continuation lines
        if LISTITEM.match(ln):
            flush()
            fam0 = "num" if re.match(r"^\s*\d", ln) else "bullet"

            def fam(l):
                return "num" if re.match(r"^\s*\d", l) else "bullet"
            block = []
            while i < n:
                mm = LISTITEM.match(lines[i])
                if mm:
                    if block and not lines[i].startswith("  ") and fam(lines[i]) != fam0:
                        break                              # list family change = new list
                    block.append(lines[i]); i += 1
                elif lines[i].strip() == "" and i + 1 < n and (
                        (LISTITEM.match(lines[i + 1]) and fam(lines[i + 1]) == fam0)
                        or lines[i + 1].startswith("  ")):
                    block.append(""); i += 1          # loose item / nested follows
                elif lines[i].startswith("  ") and block:
                    block.append(lines[i]); i += 1    # continuation (wrap or child)
                else:
                    break
            out.append(_render_list(block, lm))
            continue
        if s.startswith("<details") or s.startswith("</details") or s.startswith("<summary") \
                or s.startswith("</summary") or s.startswith("<img"):
            flush(); out.append(s); i += 1; continue
        if not s:
            flush(); i += 1; continue
        # paragraph line; trailing two spaces = hard break marker
        para.append(s + ("\x01" if ln.endswith("  ") else ""))
        i += 1
    flush()
    return "\n".join(out)


def _dedent_child(lines):
    """strip common leading indent so nested child lines re-parse."""
    widths = [len(l) - len(l.lstrip()) for l in lines if l.strip()]
    d = min(widths) if widths else 0
    return [l[d:] if len(l) >= d else l for l in lines]


def _render_list(block_lines, lm):
    # parse into items: (indent, marker, [content lines incl. raw child lines])
    items = []
    cur = None
    loose = False
    for ln in block_lines:
        mm = LISTITEM.match(ln)
        if mm:
            if cur:
                items.append(cur)
            cur = {"ind": len(mm.group(1)), "marker": mm.group(2), "lines": [mm.group(4)]}
        elif cur is not None:
            if ln.strip() == "":
                loose = True
                cur["lines"].append("")
            else:
                cur["lines"].append(ln)
    if cur:
        items.append(cur)
    if not items:
        return ""
    base = min(it["ind"] for it in items)
    block_loose = any(l.strip() == "" for l in block_lines)
    grouped = []          # [(item, [child block lines])]
    for it in items:
        if it["ind"] == base:
            grouped.append([it, []])
        elif grouped:
            grouped[-1][1].append(" " * (it["ind"] - base) + it["marker"] + " " + " ".join(l for l in it["lines"] if l.strip()))
    ordered = bool(re.match(r"\d", str(grouped[0][0]["marker"])))
    TASKHEAD = re.compile(r"^\[[ xX]\]\s")
    def is_task(it):
        first = next((l.strip() for l in it["lines"] if l.strip()), "")
        return bool(TASKHEAD.match(first))
    has_task = any(is_task(it) for it, _ in grouped)
    html_items = []
    for it, kid_lines in grouped:
        own = []
        # child *continuation* lines (indented, not new sibling markers) belong to this item
        for l in it["lines"]:
            if l.strip() and (len(l) - len(l.lstrip())) >= it["ind"] + 2:
                # could be child list or wrap; if it looks like a list marker, it's a child
                if LISTITEM.match(l.strip()):
                    kid_lines.append(l.strip())
                else:
                    own.append(l.strip())
            elif l.strip() == "":
                own.append("")
            else:
                own.append(l.strip())
        joined = "\n".join(own).strip()
        segs = [sg for sg in re.split(r"\n\s*\n", joined) if sg.strip()]
        inner = ""
        for sg in segs:
            first = sg.splitlines()[0].strip()
            if first.startswith(">"):
                inner += _blocks([re.sub(r"^\s*>?\s?", ">", x.strip()) if not x.strip().startswith(">") else x for x in sg.splitlines()], lm)
            elif LISTITEM.match(first) or ATX.match(first):
                inner += _blocks(sg.splitlines(), lm)
            elif len(segs) > 1 or block_loose:
                inner += "<p>" + inline(sg.replace("\n", " "), lm) + "</p>"
            else:
                inner += inline(sg.replace("\n", " "), lm)
        if kid_lines:
            inner += _render_list(_dedent_child(kid_lines), lm)
        cls = ' class="task"' if is_task(it) else ""
        html_items.append("<li%s>%s</li>" % (cls, inner))
    name = "ol" if ordered else "ul"
    attrs = ' class="task-list"' if has_task else ""
    start = ""
    if ordered:
        fm = re.match(r"(\d+)", str(grouped[0][0]["marker"]))
        if fm and fm.group(1) != "1":
            start = ' start="%s"' % fm.group(1)
    return "<%s%s%s>%s</%s>" % (name, attrs, start, "".join(html_items), name)


def _bare(url, keep):
    """bare-URL hit: link it, moving trailing punctuation out of the href/text."""
    core = url.rstrip(".,;:!?)")
    tail = url[len(core):]
    return keep('<a href="%s">%s</a>%s' % (core, core, tail))


def inline(s: str, link_map=None) -> str:
    """Inline pass with a stash so generated HTML is never re-matched by later rules."""
    toks = []

    def keep(fragment):
        toks.append(fragment)
        return "\x00%d\x00" % (len(toks) - 1)

    s = esc(s)                                   # escape once, first; rules emit raw HTML
    s = re.sub(r"\\\x60|\\" + r"[`*_{}\[\]()<>#+.!~|\-]", lambda m: keep(m.group(0)[1]), s)
    s = re.sub(r"(`+)(.+?)\1", lambda m: keep("<code>" + m.group(2).strip() + "</code>"), s)

    def wl(m):
        target, label = m.group(1).strip(), m.group(3)
        label = label or target.rsplit("/", 1)[-1]
        href = link_map.get(target) if link_map else None
        cls = ' class="dead"' if href is None and link_map is not None else ""
        href = href or "/view?q=" + urlquote(target)
        return keep('<a href="%s"%s>%s</a>' % (H.escape(href, quote=True), cls, label))
    s = WIKILINK.sub(wl, s)

    s = re.sub(r'!\[([^\]]*)\]\((\S+?)(?:\s+&quot;([^&]*)&quot;)?\)',
               lambda m: keep('<img src="%s" alt="%s"%s loading="lazy">' %
                              (m.group(2), m.group(1), (' title="%s"' % m.group(3)) if m.group(3) else "")), s)

    def lnk(m):
        t, url, title = m.group(1), m.group(2), m.group(3)
        return keep('<a href="%s"%s>%s</a>' % (url, (' title="%s"' % title) if title else "", t))
    s = re.sub(r'\[([^\]]+)\]\((https?://[^\)\s]+|/view[^\)\s]*)(?:\s+&quot;([^&]*)&quot;)?\)', lnk, s)

    s = re.sub(r"&lt;((?:https?|mailto):[^&\s]+?)&gt;",
               lambda m: keep('<a href="%s">%s</a>' % (m.group(1), m.group(1))), s)
    # bare URLs not already inside a generated tag: link them, trim trailing punctuation
    s = re.sub(r"https?://[^\s<>\x00]+", lambda m: _bare(m.group(0), keep), s)
    s = re.sub(r"\*\*\*(?=\S)(.+?)(?<=\S)\*\*\*(?!\*)", lambda m: keep("<strong><em>" + m.group(1) + "</em></strong>"), s)
    s = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*(?!\*)", lambda m: keep("<strong>" + m.group(1) + "</strong>"), s)
    s = re.sub(r"__(?=\S)(.+?)(?<=\S)__(?!_)", lambda m: keep("<strong>" + m.group(1) + "</strong>"), s)
    s = re.sub(r"(?<![\w*])\*(?=\S)(.+?)(?<=\S)\*(?![\w*])", lambda m: keep("<em>" + m.group(1) + "</em>"), s)
    s = re.sub(r"(?<![\w_])_(?=\S)(.+?)(?<=\S)_(?![\w_])", lambda m: keep("<em>" + m.group(1) + "</em>"), s)
    s = re.sub(r"~~(?=\S)(.+?)(?<=\S)~~", lambda m: keep("<del>" + m.group(1) + "</del>"), s)
    s = re.sub(r"\[([ xX])\]", lambda m: keep('<span class="box%s"></span>' %
               (" done" if m.group(1) != " " else "")), s)
    s = s.replace("\x01", "<br>")
    return re.sub(r"\x00(\d+)\x00", lambda m: toks[int(m.group(1))], s)


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
ul,ol{padding-left:1.4em}li{margin:.15em 0}li>p{margin:.3em 0}img{max-width:100%}hr{border:none;border-top:1px solid var(--line);margin:1.6em 0}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin:.4em 0 1.2em}.chip{background:var(--chip);border:1px solid var(--line);color:var(--dim);border-radius:999px;padding:1px 10px;font-size:12px}
.side{margin-top:2.5em;border-top:1px solid var(--line);padding-top:1em}.side h3{font-size:.85em;color:var(--dim);text-transform:uppercase;letter-spacing:.05em}
.side a{display:inline-block;margin:2px 8px 2px 0;font-size:13px}
.snip{color:var(--dim);font-size:13px;margin:.3em 0 0}
.res{margin:1.1em 0}.res .path{color:var(--dim);font-size:12px}
.muted{color:var(--dim)}
table{border-collapse:collapse;margin:1em 0;font-size:13px}th,td{border:1px solid var(--line);padding:6px 10px;text-align:left;vertical-align:top}th{background:var(--chip);color:var(--fg)}.tscroll{overflow-x:auto}
.task-list{list-style:none;padding-left:1.1em;margin:.4em 0}li.task{list-style:none;margin-left:-1.1em}.box{display:inline-block;width:.95em;height:.95em;border:2px solid #7d8590;border-radius:4px;vertical-align:-0.12em;margin-right:.45em;background:transparent}.box.done{background:#238636;border-color:#238636;position:relative}.box.done::after{content:"";position:absolute;left:.22em;top:.04em;width:.28em;height:.52em;border:solid #fff;border-width:0 2px 2px 0;transform:rotate(45deg)}del{color:var(--dim)}ul.task-list ul{list-style:disc;padding-left:1.4em;margin-left:.4em}.box.done{color:#3fb950}del{color:var(--dim)}svg{max-width:100%}
.node circle{cursor:pointer}.node text{fill:var(--fg);font-size:9px}.node text:hover,.node:hover text{fill:var(--acc)}
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


GRAPH_FONT_W = 5.0    # ~px per glyph for the 9px sans the graph labels render in
GRAPH_LABEL_H = 11.0  # vertical band a label occupies


_LAYOUT_MEMO: dict = {}


def _graph_layout(nodes: list, edges: list):
    """Deterministic layout for one graph snapshot: Fruchterman-Reingold with cooling
    plus a gentle centripetal pull (without it half the graph sticks to the clamp
    border — measured 29/63 on the live wiki, 2026-10-24) and a label-box relaxation
    pass (labels are 9px text 8px right of the dot; FR spaces dots, not labels — the
    collision Simo screenshotted). v0.4.3: the wiki only grows, so the canvas scales
    with the label area it must hold. Returns (W, Hh, pos)."""
    W = 1000
    n = max(len(nodes), 1)
    label_area = sum(8.0 + len(Path(nd["path"]).stem) * GRAPH_FONT_W for nd in nodes) * GRAPH_LABEL_H
    Hh = min(max(720.0, label_area * 3.2 / W), 4000.0)
    R = min(W, Hh) / 2.4
    pos = {}
    for i, node in enumerate(nodes):
        a = 2 * math.pi * i / n
        pos[node["path"]] = (W / 2 + R * math.cos(a), Hh / 2 + R * 0.8 * math.sin(a))
    keys = list(pos)
    k = 0.8 * math.sqrt(W * Hh / n)        # ideal edge length scales with canvas and size
    t = min(60.0, min(W, Hh) / 12.0)
    for _ in range(150):
        disp = {p: [0.0, 0.0] for p in keys}
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                a, b = keys[i], keys[j]
                dx, dy = pos[a][0] - pos[b][0], pos[a][1] - pos[b][1]
                d = math.hypot(dx, dy) or 0.1
                f = k * k / d
                ux, uy = dx / d * f, dy / d * f
                disp[a][0] += ux; disp[a][1] += uy
                disp[b][0] -= ux; disp[b][1] -= uy
        for f_, t_ in edges:
            if f_ in pos and t_ in pos:
                dx, dy = pos[f_][0] - pos[t_][0], pos[f_][1] - pos[t_][1]
                d = math.hypot(dx, dy) or 0.1
                f = d * d / k
                ux, uy = dx / d * f, dy / d * f
                disp[f_][0] -= ux; disp[f_][1] -= uy
                disp[t_][0] += ux; disp[t_][1] += uy
        for p in keys:
            dx, dy = disp[p]
            d = math.hypot(dx, dy) or 1.0
            step = min(d, t)
            x = pos[p][0] + dx / d * step + (W / 2 - pos[p][0]) * 0.01
            y = pos[p][1] + dy / d * step + (Hh / 2 - pos[p][1]) * 0.01
            pos[p] = (min(max(x, 40), W - 40), min(max(y, 40), Hh - 30))
        t = max(t * 0.96, 0.5)
    half = {p: (8.0 + len(Path(p).stem) * GRAPH_FONT_W) / 2.0 + 4.0 for p in keys}
    for _ in range(80):
        moved = 0.0
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                a, b = keys[i], keys[j]
                dx, dy = pos[a][0] - pos[b][0], pos[a][1] - pos[b][1]
                need = half[a] + half[b]
                if abs(dy) < GRAPH_LABEL_H and abs(dx) < need:
                    push = (need - abs(dx)) / 2.0 + 0.5
                    s = 1.0 if dx >= 0 else -1.0
                    pos[a] = (pos[a][0] + s * push, pos[a][1])
                    pos[b] = (pos[b][0] - s * push, pos[b][1])
                    moved += push
        if moved < 1.0:
            break
    for p in keys:
        x, y = pos[p]
        pos[p] = (min(max(x, 40), W - 40), min(max(y, 40), Hh - 30))
    return W, Hh, pos


def page_graph(core, g):
    nodes = [n for n in g["nodes"] if not n["path"].startswith("?")]
    broken = [e for e in g["edges"] if str(e[1]).startswith("?")]
    # v0.4.3 (2): the page rebuilds per request and the graph only grows — memoize the
    # deterministic layout by exact (paths, edges) content; the small cache is cleared
    # wholesale when snapshots pile up, so memory stays bounded.
    mkey = (tuple(nd["path"] for nd in nodes), tuple(tuple(e) for e in g["edges"]))
    if mkey not in _LAYOUT_MEMO:
        if len(_LAYOUT_MEMO) > 3:
            _LAYOUT_MEMO.clear()
        _LAYOUT_MEMO[mkey] = _graph_layout(nodes, g["edges"])
    W, Hh, pos = _LAYOUT_MEMO[mkey]
    lines, circles = [], []
    # v0.4.3 (3): links are directional (a's page contains [[b]]) — one-way edges get an
    # arrowhead at the target, mutual pairs stay plain lines (Simo's spec). The line ends
    # at the dot's rim so the arrowhead isn't buried under the node.
    eset = {(f_, t_) for f_, t_ in g["edges"]}
    for f_, t_ in g["edges"]:
        if f_ in pos and t_ in pos:
            (x1, y1), (x2, y2) = pos[f_], pos[t_]
            cls = "edge"
            if (t_, f_) in eset:
                lines.append(f'<line class="{cls}" x1="{x1:.0f}" y1="{y1:.0f}" x2="{x2:.0f}" y2="{y2:.0f}"/>')
            else:
                dx, dy = x2 - x1, y2 - y1
                dd = math.hypot(dx, dy) or 1.0
                ex, ey = x2 - dx / dd * 8.0, y2 - dy / dd * 8.0
                lines.append(f'<line class="{cls}" x1="{x1:.0f}" y1="{y1:.0f}" x2="{ex:.0f}" y2="{ey:.0f}" marker-end="url(#ah)"/>')
    defs = ('<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" '
            'markerHeight="6" orient="auto"><path d="M0,0L10,5L0,10z" fill="#3d444d"/></marker></defs>')
    for nd in nodes:
        x, y = pos[nd["path"]]
        colour = "#58a6ff" if nd["path"].startswith("entities/") else ("#d29922" if nd["path"].startswith("concepts/") else ("#8b949e" if nd["path"].startswith("queries/") else "#3fb950"))
        circles.append(f'<g class="node"><a href="/view?path={urlquote(nd["path"])}"><circle cx="{x:.0f}" cy="{y:.0f}" r="6" fill="{colour}"><title>{esc(nd["path"])}{esc((nd.get("title") or ""))}</title></circle>'
                       f'<text x="{x + 8:.0f}" y="{y + 3:.0f}"><tspan>{esc(Path(nd["path"]).stem)}</tspan></text></a></g>')
    svg = f'<svg viewBox="0 0 {W} {Hh}" xmlns="http://www.w3.org/2000/svg">{defs}{"".join(lines)}{"".join(circles)}</svg>'
    body = (f'<h1>graph</h1><p class="muted">{len(nodes)} nodes · {len(g["edges"])} edges · '
            f'blue entity · yellow concept · grey raw-note · green other · arrowhead = one-way link'
            f'{f" · {len(broken)} broken edges (dashed red)" if broken else ""}</p>' + svg)
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
