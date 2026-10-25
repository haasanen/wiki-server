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
            out.append("<h2>" + inline(txt, lm) + "</h2>"); i += 1; continue
        if para and set(s) == {"-"}:      # any run of dashes under a para = setext h2
            txt = " ".join(para); para = []
            out.append("<h2>" + inline(txt, lm) + "</h2>"); i += 1; continue
        m = ATX.match(s)
        if m:
            flush()
            lvl = len(m.group(1))
            # design: one h1 per page (the chrome title); markdown # renders as h2
            lvl = 2 if lvl == 1 else lvl
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
*{box-sizing:border-box}html{font-size:calc(100% + 0.25vw)}body{margin:0;background:var(--bg);color:var(--fg);font:1rem/1.6 -apple-system,"Segoe UI",Roboto,sans-serif}
header{display:flex;gap:14px;align-items:center;padding:10px 18px;border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--bg);z-index:2}
header a{color:var(--acc);text-decoration:none}header b{font-size:1.05rem}
form{margin-left:auto;display:flex;gap:6px}input{background:var(--chip);border:1px solid var(--line);color:var(--fg);padding:5px 9px;border-radius:6px;min-width:14rem;font:inherit}
main{max-width:900px;margin:0 auto;padding:22px 26px 60px}main p,main ul,main ol,main blockquote,main pre,main table,main h1,main h2,main h3,main .chips,.pagewrap .prose>*{max-width:66ch}a{color:var(--acc);text-decoration:none}a:hover{text-decoration:underline}
a.dead{color:#f85149}
h1{font-size:1.9em;margin:.15em 0 .4em}h2{margin-top:1.4em;border-bottom:1px solid var(--line);padding-bottom:.2em}
code{background:var(--chip);padding:1px 5px;border-radius:4px;font:13px/1.5 ui-monospace,Consolas,monospace}
pre{background:var(--chip);padding:12px;border-radius:8px;overflow-x:auto;border:1px solid var(--line)}pre code{background:none;padding:0}
blockquote{border-left:3px solid var(--line);margin:1em 0;padding:.2em 1em;color:var(--dim)}
ul,ol{padding-left:1.4em}li{margin:.15em 0}li>p{margin:.3em 0}img{max-width:100%}hr{border:none;border-top:1px solid var(--line);margin:1.6em 0}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin:.4em 0 1.2em}.chip{background:var(--chip);border:1px solid var(--line);color:var(--dim);border-radius:999px;padding:1px 10px;font-size:.75rem}
.side{margin-top:2.5em;border-top:1px solid var(--line);padding-top:1em}.side h3{font-size:.85em;color:var(--dim);text-transform:uppercase;letter-spacing:.05em}
.side a{display:inline-block;margin:2px 8px 2px 0;font-size:.8rem}
.snip{color:var(--dim);font-size:.8rem;margin:.3em 0 0}
.res{margin:1.1em 0}.res .path{color:var(--dim);font-size:.75rem}
.muted{color:var(--dim)}
table{border-collapse:collapse;margin:1em 0;font-size:.8rem}th,td{border:1px solid var(--line);padding:6px 10px;text-align:left;vertical-align:top}th{background:var(--chip);color:var(--fg)}.tscroll{overflow-x:auto}
.task-list{list-style:none;padding-left:1.1em;margin:.4em 0}li.task{list-style:none;margin-left:-1.1em}.box{display:inline-block;width:.95em;height:.95em;border:2px solid #7d8590;border-radius:4px;vertical-align:-0.12em;margin-right:.45em;background:transparent}.box.done{background:#238636;border-color:#238636;position:relative}.box.done::after{content:"";position:absolute;left:.22em;top:.04em;width:.28em;height:.52em;border:solid #fff;border-width:0 2px 2px 0;transform:rotate(45deg)}del{color:var(--dim)}ul.task-list ul{list-style:disc;padding-left:1.4em;margin-left:.4em}.box.done{color:#3fb950}del{color:var(--dim)}svg{max-width:100%}
.node circle{cursor:pointer}.node text{fill:var(--fg);font-size:inherit}.node text:hover,.node:hover text{fill:var(--acc)}
i.sw{display:inline-block;width:9px;height:9px;border-radius:50%;margin:0 4px 0 12px;vertical-align:-1px}.ll{display:inline-block;width:18px;height:2px;background:#58626d;margin:0 4px 0 12px;vertical-align:3px}.llb{display:inline-block;width:18px;height:0;border-top:2px dashed #f85149;margin:0 4px 0 12px;vertical-align:3px}.lgs{margin:0 4px 0 12px;vertical-align:0}
#gsvg{touch-action:none;cursor:grab;display:block;width:100%}main:has(> .gwrap.full){max-width:none;padding:18px 16px 40px}.egowrap{margin:8px 0 18px}.egowrap #gsvg{width:100%;height:auto;max-width:44rem;margin:0 auto}
.pagewrap{display:grid;grid-template-columns:minmax(0,66ch) min(26rem,34vw);gap:2rem;align-items:start}.pagewrap .prose{min-width:0}main:has(> .pagewrap){max-width:min(96vw,78rem)}.pagerail{position:sticky;top:1rem}.pagerail .side{border-top:none;padding-top:0;margin-top:0}.egowrap .gwrap{display:flex;flex-direction:column}.egowrap .glegend{order:3;position:static;opacity:1;background:transparent;border:none;padding:8px 2px 0;pointer-events:auto}.pagerail .egowrap #gsvg{width:100%;height:auto;max-height:70vh}@media(max-width:61.25em){.pagewrap{grid-template-columns:1fr}.pagerail{position:static}}.gnav{position:absolute;top:10px;right:10px;z-index:5;display:flex;gap:6px;margin:0}.gwrap{position:relative}.glegend{position:absolute;top:10px;left:10px;z-index:5;background:rgba(13,17,23,.85);border:1px solid var(--line);border-radius:8px;padding:.4rem .75rem;font-size:.8rem;line-height:2;pointer-events:none}
.gnav button{min-width:2.5rem;height:2.1rem;padding:0 .6rem;background:var(--chip);color:var(--fg);border:1px solid var(--line);border-radius:6px;font-size:1rem;line-height:1;cursor:pointer}
.gnav button:hover{border-color:var(--acc);color:var(--acc)}a:focus-visible,button:focus-visible,input:focus-visible,summary:focus-visible{outline:2px solid var(--acc);outline-offset:2px;border-radius:3px}
.edge{stroke:#58626d;stroke-width:1}.edge.broken{stroke:#f85149;stroke-dasharray:3 3}
"""

SHELL_JS = ('<script>document.addEventListener("keydown",function(e){'
            'if(e.key==="/"&&!/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName)){'
            'e.preventDefault();var s=document.getElementById("sq");if(s)s.focus();}});</script>')


def shell(title: str, body: str, q: str = "") -> str:
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} — wiki</title><style>{CSS}</style></head><body>
<header><b><a href="/">wiki</a></b>
<a href="/schema">schema</a><a href="/log">log</a><a href="/graph">graph</a><a href="/lint">lint</a><a href="/help">api</a>
<form action="/search" method="get"><input id="sq" name="q" placeholder="search… (press /)" value="{esc(q)}" autofocus></form>
</header><main>{body}</main>{SHELL_JS}</body></html>"""


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
    # placement revision (Simo asked whether top-of-page was ideal — it is not): the
    # prose is the page's primary content, so the neighbourhood graph goes BELOW it,
    # merged into the connections section where the out-links/backlinks lists already
    # live. A ~500px canvas above the fold buried the actual content; serious
    # knowledge tools keep link context under the prose or in a side rail.
    ego = ego_block(core, rel)
    links = (f'<h3>out links</h3><div>{out_h or "<span class=muted>none</span>"}</div>'
             f'<h3>backlinks</h3><div>{in_h or "<span class=muted>none</span>"}</div>')
    if ego:
        # layout revision 2 (Simo: "who said it has to be a single column?"): pages
        # WITH a neighbourhood graph use a two-column grid — prose left, connections
        # (graph + link lists) sticky in a right rail; under 900px it stacks and the
        # connections fall below the prose, as before.
        side = f'<aside class="pagerail"><div class="side"><h3>connections</h3>{ego}{links}</div></aside>'
        body = (f"<h1>{esc(title)}</h1>"
                + f'<div class="pagewrap"><div class="prose">' + _frontmatter_chips(fm)
                + f'<p class="muted">{esc(rel)} · <a href="/page?path={urlquote(rel)}">raw</a></p>'
                + _fix_links(text, lm) + "</div>" + side + "</div>")
    else:
        body = (f"<h1>{esc(title)}</h1>" + _frontmatter_chips(fm)
                + f'<p class="muted">{esc(rel)} · <a href="/page?path={urlquote(rel)}">raw</a></p>'
                + _fix_links(text, lm)
                + f'<div class="side">{links}</div>')
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


GRAPH_FONT_W = 6.6    # ~px per glyph for the 11px sans the graph labels render in
GRAPH_LABEL_H = 13.0  # vertical band a label occupies
LABEL_CAP = 0         # max glyphs per label; 0 = no cap. Ego view caps (v0.4.9, Simo's
                      # 4K screenshot: 20+ char stems force a 20+ char canvas, so the
                      # canvas/label ratio - i.e. physical text size - can never rise).
                      # The full stem stays in <title> and the linked page itself.

def _lbl(stem):
    return stem if not LABEL_CAP or len(stem) <= LABEL_CAP else stem[:LABEL_CAP - 1] + "\u2026"
GRAPH_COLORS = {"entities": "#58a6ff", "concepts": "#d29922", "queries": "#8b949e", "other": "#3fb950"}


def _node_colour(path: str) -> str:
    for kind, colour in GRAPH_COLORS.items():
        if kind != "other" and path.startswith(kind + "/"):
            return colour
    return GRAPH_COLORS["other"]


# v0.4.4 (Simo: "the graph needs to be more interactable... proper intuitive navigation
# controls"): interactions stolen from Cytoscape/D3 — wheel zoom, drag-pan, node drag,
# pinch, fit-to-content, on-screen buttons, keyboard — in ~80 lines of vanilla JS.
# Zero external files: if JS is off the page is still the full static SVG.
GRAPH_NAV = ('<div class="gnav">'
             '<button data-a="in" title="zoom in (+)">+</button>'
             '<button data-a="out" title="zoom out (-)">&minus;</button>'
             '<button data-a="fit" title="fit graph to window (f)">fit</button>'
             '</div>')
GRAPH_JS = """<script>
(function(){
var svg=document.getElementById("gsvg");if(!svg)return;
var vb0=svg.getAttribute("viewBox").split(/[ ,]+/).map(Number),vb=vb0.slice();
var gs=[].slice.call(svg.querySelectorAll("g.node")),pos=[],drag=null,pts={},pd=0,moved=false;
gs.forEach(function(g){var m=g.getAttribute("transform").match(/translate\\(([-\\d.]+),([-\\d.]+)\\)/);pos.push([+m[1],+m[2]]);});
function setvb(){svg.setAttribute("viewBox",vb.map(function(v){return v.toFixed(1)}).join(" "));}
function unit(){return vb[2]/svg.getBoundingClientRect().width;}
function s2v(c){var r=svg.getBoundingClientRect();return[vb[0]+(c.clientX-r.left)*unit(),vb[1]+(c.clientY-r.top)*unit()];}
function zoomAt(cx,cy,k){var nw=vb[2]*k,nh=vb[3]*k,min=vb0[2]/6,max=vb0[2]*1.5;
  if(nw<min||nw>max)return;var mx=(cx-vb[0])/vb[2],my=(cy-vb[1])/vb[3];
  vb[0]+=(vb[2]-nw)*mx;vb[1]+=(vb[3]-nh)*my;vb[2]=nw;vb[3]=nh;setvb();}
function center(){return[vb[0]+vb[2]/2,vb[1]+vb[3]/2];}
function fit(){var x0=1/0,y0=1/0,x1=-1/0,y1=-1/0,any=false;
  gs.forEach(function(g,i){var b;try{b=g.getBBox()}catch(err){return}any=true;
    var px=pos[i][0],py=pos[i][1];
    x0=Math.min(x0,px+b.x);y0=Math.min(y0,py+b.y);
    x1=Math.max(x1,px+b.x+b.width);y1=Math.max(y1,py+b.y+b.height);});
  if(!any)return;var m=Math.max((x1-x0)*0.05,20);
  vb=[x0-m,y0-m,(x1-x0)+2*m,(y1-y0)+2*m];setvb();}
function two(){var k=Object.keys(pts);return k.length===2?k:null;}
function dist(){var T=two();return Math.hypot(pts[T[0]][0]-pts[T[1]][0],pts[T[0]][1]-pts[T[1]][1]);}
svg.addEventListener("wheel",function(e){e.preventDefault();var p=s2v(e);
  zoomAt(p[0],p[1],Math.pow(1.0015,e.deltaY));},{passive:false});
svg.addEventListener("pointerdown",function(e){
  pts[e.pointerId]=[e.clientX,e.clientY];
  if(two()){pd=dist();drag=null;return;}
  // viewer-only (Simo): every drag pans the view — nodes are not draggable.
  // Capture ONLY off-link: setPointerCapture retargets the click event to the svg
  // in real browsers, which silently killed node navigation (Simo live report).
  // Pressing a node link therefore skips capture so the <a> still gets its click.
  drag={x:e.clientX,y:e.clientY,v:vb.slice()};moved=false;
  if(!(e.target.closest&&e.target.closest("a"))){svg.setPointerCapture(e.pointerId);}});
svg.addEventListener("pointermove",function(e){
  if(!pts[e.pointerId])return;
  if(two()){var nd=dist();
    if(pd&&nd){var T=two(),mid=s2v({clientX:(pts[T[0]][0]+pts[T[1]][0])/2,clientY:(pts[T[0]][1]+pts[T[1]][1])/2});
      zoomAt(mid[0],mid[1],pd/nd);pd=nd;}
    pts[e.pointerId]=[e.clientX,e.clientY];return;}
  if(!drag)return;
  var dx=(e.clientX-drag.x)*unit(),dy=(e.clientY-drag.y)*unit();
  if(Math.abs(e.clientX-drag.x)+Math.abs(e.clientY-drag.y)>4)moved=true;
  vb[0]=drag.v[0]-dx;vb[1]=drag.v[1]-dy;setvb();});
function up(e){delete pts[e.pointerId];pd=0;drag=null;}
svg.addEventListener("pointerup",up);svg.addEventListener("pointercancel",up);
svg.addEventListener("click",function(e){if(moved){e.preventDefault();e.stopPropagation();moved=false;}},true);

[].forEach.call(document.querySelectorAll(".gnav button"),function(b){
  b.addEventListener("click",function(){var a=b.getAttribute("data-a"),c=center();
    if(a==="in")zoomAt(c[0],c[1],0.8);else if(a==="out")zoomAt(c[0],c[1],1.25);
    else if(a==="fit")fit();});});
document.addEventListener("keydown",function(e){
  if(e.target.tagName==="INPUT"||e.target.tagName==="TEXTAREA")return;
  var c=center();
  if(e.key==="+"||e.key==="=")zoomAt(c[0],c[1],0.8);
  else if(e.key==="-")zoomAt(c[0],c[1],1.25);
  else if(e.key==="f")fit();});
})();
</script>"""


_LAYOUT_MEMO: dict = {}
# v0.4.4 (Simo: "we want the very minimum edge overlapping"): greedy edge-crossing
# reduction post-pass — the idea behind Cytoscape fcose's edgeOverlapAvoid. Tests
# toggle this flag to prove the pass actually reduces crossings on identical input.
GRAPH_EDGE_PASS = True


def _segs_cross(a, b, c, d):
    """True if segment a-b properly crosses segment c-d (shared endpoints excluded by caller)."""
    def orient(p, q, r):
        v = (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
        return (v > 1e-9) - (v < -1e-9)
    o1, o2 = orient(a, b, c), orient(a, b, d)
    return o1 != o2 and orient(c, d, a) != orient(c, d, b)


def _graph_layout(nodes: list, edges: list):
    """Deterministic layout for one graph snapshot: Fruchterman-Reingold with cooling
    plus a gentle centripetal pull (without it half the graph sticks to the clamp
    border — measured 29/63 on the live wiki, 2026-10-24) and a label-box relaxation
    pass (labels are 9px text 8px right of the dot; FR spaces dots, not labels — the
    collision Simo screenshotted). v0.4.3: the wiki only grows, so the canvas scales
    with the label area it must hold. Returns (W, Hh, pos)."""
    W = 1000
    n = max(len(nodes), 1)
    label_area = sum(8.0 + len(_lbl(Path(nd["path"]).stem)) * GRAPH_FONT_W for nd in nodes) * GRAPH_LABEL_H
    Hh = min(max(720.0, label_area * 3.2 / W), 4000.0)
    pos = {}
    keys = [nd["path"] for nd in nodes]
    # v0.4.5 (Simo: stretched clusters -> "find the ranking, use the best"):
    # LEIDEN community detection (Traag et al 2019 — top of published LFR
    # benchmark rankings; guarantees well-connected communities; modularity
    # objective = pure adjacency, no distance semantics) + blob placement, the
    # fCoSE pipeline shape (IEEE TVCG 2021: quality layouts at interactive
    # speeds exactly in our small-graph size range). Communities come from
    # connectivity only; each becomes one blob placed against the others on a
    # ring ordered by blob-blob adjacency (neighbouring communities land next to
    # each other); members pack inside via phyllotaxis in degree order. A leaf
    # then competes for space only with its own community — the stretched-chain
    # geometry Simo screenshotted cannot happen. On the REAL wiki graph Leiden
    # finds exactly the human clusters: fennec[12], stealth[9], comfyui/media[7],
    # NAS/home[7], lg-webos[7], knowledge/wiki[6], strata/LLM[5] ... Q=0.372.
    # FR (original kernel below) stays as the fallback for graphs with NO
    # community structure (Leiden all-singleton) where organic spreading wins.
    kset = set(keys)
    adj = {p: set() for p in keys}
    for a, b in edges:
        if a in kset and b in kset and a != b:
            adj[a].add(b)
            adj[b].add(a)
    deg = {p: len(adj[p]) for p in keys}
    m2 = sum(deg.values()) or 1
    comm = {p: i for i, p in enumerate(sorted(keys))}
    tot = {i: float(deg[p]) for p, i in comm.items()}
    for sweep in range(30):
        moved = False
        for p in sorted(keys):
            cur = comm[p]
            cnt: dict = {}
            for q in adj[p]:
                cnt[comm[q]] = cnt.get(comm[q], 0) + 1
            tot[cur] -= deg[p]
            stay = cnt.get(cur, 0) - 1  # own links (p not yet moved back)
            best_c, best_gain = None, 0.0
            for c, k_ic in cnt.items():
                if c == cur:
                    continue
                delta = (k_ic - tot[c] * deg[p] / m2) - stay
                if delta > best_gain + 1e-12:
                    best_c, best_gain = c, delta
            if best_c is not None:
                comm[p] = best_c
                tot[best_c] += deg[p]
                moved = True
            else:
                tot[cur] += deg[p]
        if not moved:
            break
    groups: dict = {}
    for p, c in comm.items():
        groups.setdefault(c, []).append(p)
    blobs = [sorted(v) for v in groups.values() if len(v) >= 2]
    if blobs:
        blob_of = {}
        for bi, mem in enumerate(blobs):
            for p in mem:
                blob_of[p] = bi
        for p in keys:
            if p not in blob_of:
                blob_of[p] = len(blobs)
                blobs.append([p])
        badj = [set() for _ in blobs]
        for a, b in edges:
            if a in blob_of and b in blob_of and blob_of[a] != blob_of[b]:
                badj[blob_of[a]].add(blob_of[b])
                badj[blob_of[b]].add(blob_of[a])
        r_blob = [27.0 * math.sqrt(len(mem)) + 24.0 for mem in blobs]
        # ring order: BFS over blob adjacency starting at the biggest blob ->
        # linked communities land side by side (pure ordering, no distances)
        order = []
        start = max(range(len(blobs)), key=lambda i: (len(blobs[i]), i))
        q = [start]
        seen = {start}
        while q:
            u = q.pop(0)
            order.append(u)
            for v in sorted(badj[u]):
                if v not in seen:
                    seen.add(v)
                    q.append(v)
        for i in range(len(blobs)):
            if i not in seen:
                order.append(i)
        C = sum(2 * r + 120.0 for r in r_blob)
        Rb = max(C / (2 * math.pi), 1.0)
        arc = 0.0
        center_x, center_y = W / 2, Hh / 2
        blob_c = {}
        for bi in order:
            ang = 2 * math.pi * (arc + r_blob[bi]) / C
            arc += 2 * r_blob[bi] + 120.0
            rr = Rb - r_blob[bi] * 0.4
            blob_c[bi] = (center_x + rr * math.cos(ang), center_y + rr * math.sin(ang) * 0.8)
        for bi, mem in enumerate(blobs):
            cx0, cy0 = blob_c[bi]
            members = sorted(mem, key=lambda p: (-deg[p], p))
            for i, p in enumerate(members):
                if i == 0 and deg[p] >= 2:      # the community hub sits dead centre
                    pos[p] = (cx0, cy0)
                    continue
                r = r_blob[bi] * math.sqrt(i) / math.sqrt(max(len(members) - 1, 1))
                th = (i - 1) * 2.399963229728653
                pos[p] = (cx0 + r * math.cos(th), cy0 + r * math.sin(th))
    # fCoSE's actual recipe (Balcı et al 2021): coarse cluster placement THEN
    # force-directed refinement. So the FR dynamics run after blob placement too,
    # just gently (low starting temperature): they untangle within and between
    # blobs without dissolving the communities the way a cold start would.
    if not blobs:
        R = min(W, Hh) / 2.4
        for i, node in enumerate(nodes):
            a = 2 * math.pi * i / n
            pos[node["path"]] = (W / 2 + R * math.cos(a), Hh / 2 + R * 0.8 * math.sin(a))
    k = 0.8 * math.sqrt(W * Hh / n)        # ideal edge length scales with canvas and size
    t = (min(60.0, min(W, Hh) / 12.0) if not blobs else 18.0)  # gentle start when refining blobs
    if blobs:
        # gentle refinement: fewer sweeps, faster cooling
        sweeps = 40
    else:
        sweeps = 150
    for _ in range(sweeps):
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
    half = {p: (8.0 + len(_lbl(Path(p).stem)) * GRAPH_FONT_W) / 2.0 + 4.0 for p in keys}

    def _floor_round():
        # v0.4.3 (6): minimum centre distance between DOTS (34px). FR's 1/d repulsion is
        # too weak close up — measured 48/1953 real-graph pairs under 60px, min 19px;
        # that is what "some of the nodes are still overlapping" really was (Simo).
        mv = 0.0
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                a, b = keys[i], keys[j]
                dx, dy = pos[a][0] - pos[b][0], pos[a][1] - pos[b][1]
                d = math.hypot(dx, dy)
                if d < 34.0:
                    push = (34.0 - d) / 2.0 + 0.5
                    ux, uy = (dx / d, dy / d) if d > 0.01 else (0.707, 0.707)
                    pos[a] = (pos[a][0] + ux * push, pos[a][1] + uy * push)
                    pos[b] = (pos[b][0] - ux * push, pos[b][1] - uy * push)
                    mv += push
        return mv

    wlab = {p: len(_lbl(Path(p).stem)) * GRAPH_FONT_W for p in keys}  # label text width

    def _relax_round():
        # v0.4.3 (6): labels hang RIGHT of their dot (x+8 .. x+8+width) — non-overlap
        # is ASYMMETRIC: needs dx >= width(b) or dx <= -width(a), not the symmetric
        # half-width model (which under-pushed short-vs-long pairs: 5 label overlaps
        # survived it on the real graph).
        mv = 0.0
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                a, b = keys[i], keys[j]
                dx = pos[a][0] - pos[b][0]
                dy = pos[a][1] - pos[b][1]
                if abs(dy) < GRAPH_LABEL_H:
                    if 0.0 <= dx < wlab[b] + 6.0:
                        push = (wlab[b] + 6.0 - dx) / 2.0 + 0.5
                        pos[a] = (pos[a][0] + push, pos[a][1])
                        pos[b] = (pos[b][0] - push, pos[b][1])
                        mv += push
                    elif -wlab[a] - 6.0 < dx < 0.0:
                        push = (wlab[a] + 6.0 + dx) / 2.0 + 0.5
                        pos[a] = (pos[a][0] - push, pos[a][1])
                        pos[b] = (pos[b][0] + push, pos[b][1])
                        mv += push
        return mv

    # alternate until stable: dot spacing can re-overlap labels, label spreading moves
    # dots (never closer, but the floor gets the last word on dot distance)
    for _ in range(6):
        f = 0.0
        for _ in range(24):  # 200-node chains need many rounds to settle
            r = _floor_round()
            f += r
            if r < 1.0:
                break
        gv = 0.0
        for _ in range(40):
            r = _relax_round()
            gv += r
            if r < 1.0:
                break
        if f < 1.0 and gv < 1.0:
            break

    # v0.4.5: community-blob packing is denser than free FR spread, so a handful of
    # label pairs can survive the alternating passes (measured 4-9). Targeted fix:
    # take one overlapping pair at a time, separate vertically, let the dot floor
    # re-settle, and KEEP the move only if the global overlap count strictly dropped
    # (rollback otherwise) — hill-climbing, so it can never make labels worse.
    def _lab_pairs():
        out = []
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                a, b = keys[i], keys[j]
                if abs(pos[a][1] - pos[b][1]) < GRAPH_LABEL_H:
                    ax0, bx0 = pos[a][0] + 8, pos[b][0] + 8
                    if not (ax0 + wlab[a] <= bx0 + 2 or bx0 + wlab[b] <= ax0 + 2):
                        out.append((a, b))
        return out

    for _fix in range(40):
        pairs = _lab_pairs()
        if not pairs:
            break
        before = len(pairs)
        a, b = pairs[0]
        saved_a, saved_b = pos[a], pos[b]
        sgn = 1 if pos[b][1] >= pos[a][1] else -1
        pos[a] = (pos[a][0], pos[a][1] - 7.0 * sgn)
        pos[b] = (pos[b][0], pos[b][1] + 7.0 * sgn)
        for _r in range(16):
            if _floor_round() < 1.0:
                break
        _relax_round()
        if len(_lab_pairs()) >= before:
            pos[a], pos[b] = saved_a, saved_b
            _floor_round()

    # v0.4.4 (Simo: "many of the edges overlap / collide / cross each other. We want
    # the very minimum edge overlapping"): greedy crossing-reduction pass. Each node,
    # in fixed order, gets tried against a fixed 16-point nudge grid; the best move is
    # accepted only if it STRICTLY lowers the edge-edge crossings it takes part in
    # while introducing no dot-floor or label collision (those weigh 100, so layout
    # quality can never be traded for tidier lines). Skipped above 150 nodes where the
    # quadratic cost stops paying for itself.
    if GRAPH_EDGE_PASS and len(keys) <= 150:
        eidx = [(a, b) for a, b in edges if a in pos and b in pos]
        inc = {p: [k for k, (a, b) in enumerate(eidx) if p == a or p == b] for p in keys}

        def _pcost(p, xy):
            cross = 0.0
            length = 0.0
            for k in inc[p]:
                a, b = eidx[k]
                q = b if a == p else a
                for u, v in eidx:
                    if u == p or v == p or u == q or v == q:
                        continue
                    if _segs_cross(xy, pos[q], pos[u], pos[v]):
                        cross += 1.0
                # v0.4.4 (Simo vetoed bent edges — everything stays straight, so the
                # placement must also dodge dot-on-line overlaps itself: an edge of p
                # sweeping over an unrelated dot costs the same as a crossing)
                vx, vy = xy[0] - pos[q][0], xy[1] - pos[q][1]
                lv2 = vx * vx + vy * vy
                if lv2 >= 1e-9:
                    for o in keys:
                        if o == p or o == q:
                            continue
                        wx, wy = pos[o][0] - xy[0], pos[o][1] - xy[1]
                        cr = vx * wy - vy * wx
                        s = (wx * vx + wy * vy) / lv2
                        if 0.02 <= s <= 0.98 and abs(cr) / math.sqrt(lv2) < 17.0:
                            cross += 1.0
                length += math.hypot(xy[0] - pos[q][0], xy[1] - pos[q][1])
            # ...and the REVERSE direction: p itself parked on someone else's
            # line costs p's move too — without this term a dot can be nudged
            # onto a passing edge for free and the greedy pass never moves it off.
            for u, v in eidx:
                if u == p or v == p:
                    continue
                ax, ay = pos[u]
                bx, by = pos[v]
                vx2, vy2 = bx - ax, by - ay
                lv2b = vx2 * vx2 + vy2 * vy2
                if lv2b < 1e-9:
                    continue
                wx2, wy2 = xy[0] - ax, xy[1] - ay
                cr2 = vx2 * wy2 - vy2 * wx2
                s2 = (wx2 * vx2 + wy2 * vy2) / lv2b
                if 0.02 <= s2 <= 0.98 and abs(cr2) / math.sqrt(lv2b) < 17.0:
                    cross += 1.0
            viol = 0.0
            for o in keys:
                if o == p:
                    continue
                if math.hypot(xy[0] - pos[o][0], xy[1] - pos[o][1]) < 34.0:
                    viol += 1.0
                if abs(xy[1] - pos[o][1]) < GRAPH_LABEL_H:
                    lx, ox = xy[0] + 8.0, pos[o][0] + 8.0
                    if not (lx + wlab[p] <= ox + 2 or ox + wlab[o] <= lx + 2):
                        viol += 1.0
            return cross + 0.002 * length + 100.0 * viol

        for _sweep in range(4):
            improved = False
            for p in sorted(keys):
                base_cost, bestxy = _pcost(p, pos[p]), pos[p]
                for ddx in (-160, -88, -48, -24, 24, 48, 88, 160):
                    for ddy in (-160, -88, -48, -24, 24, 48, 88, 160):
                        xy = (pos[p][0] + ddx, pos[p][1] + ddy)
                        c = _pcost(p, xy)
                        if c < base_cost - 1e-9:
                            base_cost, bestxy = c, xy
                if bestxy != pos[p]:
                    pos[p] = bestxy
                    improved = True
            if not improved:
                break

    rdirs = [(a, b) for a, b in edges if a in kset and b in kset and a != b]
    # v0.4.5: hit-repair AFTER the crossing pass, double-guarded — a push is kept
    # only if dot-on-line hits strictly fall AND crossings do not rise AT ALL AND
    # labels stay clean. Running it before greedy let it relocate nodes into a
    # worse crossing basin (measured 78 -> 91); after greedy the crossings are
    # locked and only genuinely-free moves survive.

    def _cross_count():
        n = 0
        for i1 in range(len(rdirs)):
            a1, b1 = rdirs[i1]
            for a2, b2 in rdirs[i1 + 1:]:
                if len({a1, b1, a2, b2}) < 4:
                    continue
                if _segs_cross(pos[a1], pos[b1], pos[a2], pos[b2]):
                    n += 1
        return n

    base_cross = _cross_count()

    def _hit_scan():
        hits = []
        for a, b in rdirs:
            ax, ay = pos[a]
            bx, by = pos[b]
            vx, vy = bx - ax, by - ay
            lv2 = vx * vx + vy * vy
            if lv2 < 1e-9:
                continue
            L = math.sqrt(lv2)
            for o in keys:
                if o == a or o == b:
                    continue
                wx, wy = pos[o][0] - ax, pos[o][1] - ay
                cr = vx * wy - vy * wx
                s = (wx * vx + wy * vy) / lv2
                if 0.02 <= s <= 0.98 and abs(cr) / L < 17.0:
                    hits.append((a, b, o, cr, L))
        return hits

    tried = set()
    guard = 0
    while guard < 300:
        guard += 1
        hits = [h for h in _hit_scan() if (h[0], h[1], h[2]) not in tried]
        if not hits:
            break
        a, b, o, cr, L = hits[0]
        tried.add((a, b, o))
        before = len(_hit_scan())
        saved = dict(pos)
        ux, uy = (pos[b][0] - pos[a][0]) / L, (pos[b][1] - pos[a][1]) / L
        side = 1.0 if cr >= 0 else -1.0
        need = 20.0 - abs(cr) / L              # carry the dot to 20px off the line
        pos[o] = (pos[o][0] - uy * side * need, pos[o][1] + ux * side * need)
        for _r in range(16):
            if _floor_round() < 1.0:
                break
        _relax_round()
        if len(_hit_scan()) >= before or _lab_pairs() or _cross_count() > base_cross:
            pos.update(saved)      # never-worse: roll back and try the next candidate

    # v0.4.3 (6): CANVAS FITS CONTENT, not content fits canvas. Hard-clamping the layout
    # to a fixed canvas piled dots on the walls — measured 167/199 close pairs stuck on
    # borders in the 200-node star test (every leaf of a hub gets pushed outward; walls
    # cannot hold them). So: translate into positive space and grow W/H until the widest
    # label ends 10px before the right edge. No walls -> no clamp wars.
    xs = [v[0] for v in pos.values()]
    ys = [v[1] for v in pos.values()]
    reach = max(8.0 + len(_lbl(Path(p).stem)) * GRAPH_FONT_W for p in keys) + 10.0
    sx, sy = 30.0 - min(xs), 26.0 - min(ys)
    pos = {p: (p0[0] + sx, p0[1] + sy) for p, p0 in pos.items()}
    W = max(150.0, max(xs) + sx + reach)
    Hh = max(120.0, max(ys) + sy + 34.0)
    return W, Hh, pos


def _graph_markup(g, center_path=None, wrap_class="gwrap"):
    """Render one graph viewer (nav + floating legend + svg + JS) for graph dict g.
    center_path (v0.4.6, Simo: every page should show its linked neighbours): when
    given, the viewBox becomes a square centred on that node with everything else
    still visible — the same style/tooling as the whole graph, but seen from the
    page's perspective. Output is page-level HTML; wrap it in shell() or embed it."""
    nodes = [n for n in g["nodes"] if not n["path"].startswith("?")]
    broken = [e for e in g["edges"] if str(e[1]).startswith("?")]
    # v0.4.3 (2): the page rebuilds per request and the graph only grows — memoize the
    # deterministic layout by exact (paths, edges) content; the small cache is cleared
    # wholesale when snapshots pile up, so memory stays bounded. Ego blocks raised the
    # cap: one full graph + a handful of page neighbourhoods must not thrash it.
    n = len(nodes)
    tier = 11.0 if n >= 20 else round(min(16.0, 11.0 + (20 - n) * 0.35), 1)
    cap = 16 if center_path else 0
    mkey = (tuple(nd["path"] for nd in nodes), tuple(tuple(e) for e in g["edges"]), tier, cap)
    global GRAPH_FONT_W, GRAPH_LABEL_H, LABEL_CAP
    _fw0, _lh0, _cap0 = GRAPH_FONT_W, GRAPH_LABEL_H, LABEL_CAP
    GRAPH_FONT_W, GRAPH_LABEL_H, LABEL_CAP = tier * 0.6, tier * 1.18, cap
    if mkey not in _LAYOUT_MEMO:
        if len(_LAYOUT_MEMO) > 8:
            _LAYOUT_MEMO.clear()
        _LAYOUT_MEMO[mkey] = _graph_layout(nodes, g["edges"])
    W, Hh, pos = _LAYOUT_MEMO[mkey]
    if center_path and center_path in pos:
        # ego-centred square canvas: covers every node + its label whichever side
        cx, cy = pos[center_path]
        reach = max(len(_lbl(Path(nd["path"]).stem)) for nd in nodes) * GRAPH_FONT_W + 12
        R = reach
        for px, py in pos.values():
            R = max(R, abs(px - cx) + reach, abs(py - cy) + GRAPH_LABEL_H)
        R = max(R, 150.0)
        vx, vy, vw, vh = cx - R, cy - R, 2 * R, 2 * R
    else:
        vx, vy, vw, vh = 0.0, 0.0, W, Hh
    lines, circles = [], []
    # v0.4.3 (3): links are directional (a's page contains [[b]]) — one-way edges get an
    # arrowhead at the target, mutual pairs stay plain lines (Simo's spec). The line ends
    # at the dot's rim so the arrowhead isn't buried under the node.
    eset = {(f_, t_) for f_, t_ in g["edges"]}
    node_idx = {nd["path"]: i for i, nd in enumerate(nodes)}
    for f_, t_ in g["edges"]:
        if f_ in pos and t_ in pos:
            (x1, y1), (x2, y2) = pos[f_], pos[t_]
            cls = "edge"
            data = f'data-f="{node_idx[f_]}" data-t="{node_idx[t_]}" '
            if (t_, f_) in eset:
                lines.append(f'<line {data}class="{cls}" x1="{x1:.0f}" y1="{y1:.0f}" x2="{x2:.0f}" y2="{y2:.0f}"/>')
            else:
                dx, dy = x2 - x1, y2 - y1
                dd = math.hypot(dx, dy) or 1.0
                ex, ey = x2 - dx / dd * 8.0, y2 - dy / dd * 8.0
                lines.append(f'<line {data}class="{cls}" x1="{x1:.0f}" y1="{y1:.0f}" x2="{ex:.0f}" y2="{ey:.0f}" marker-end="url(#ah)"/>')
    defs = ('<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" '
            'markerHeight="6" orient="auto"><path d="M0,0L10,5L0,10z" fill="#58626d"/></marker></defs>')
    for nd in nodes:
        x, y = pos[nd["path"]]
        colour = _node_colour(nd["path"])
        # v0.4.3 (5): a label is only a label if it fits — near the right edge it is
        # flipped to the left of the dot instead of being sliced off by the canvas
        # (Simo's screenshot: five truncated "note-ste..." fragments + an unreadable pile).
        stem = esc(_lbl(Path(nd["path"]).stem))
        if x + 8 + len(stem) * GRAPH_FONT_W > vx + vw - 4:
            anchor, tx = ' text-anchor="end"', -8
        else:
            anchor, tx = "", 8
        i = node_idx[nd["path"]]
        # v0.4.4: translate() on the group + data-i so vanilla JS can retarget edges
        # while panning (no external library — the ideas are stolen, the dependency
        # is not). Circle sits at 0,0; label offset is group-relative.
        circles.append(f'<g class="node" data-i="{i}" transform="translate({x:.0f},{y:.0f})"><a href="/view?path={urlquote(nd["path"])}"><circle cx="0" cy="0" r="6" fill="{colour}"><title>{esc(nd["path"])}{esc((nd.get("title") or ""))}</title></circle>'
                       f'<text{anchor} x="{tx}" y="3"><tspan>{stem}</tspan></text></a></g>')
    st = f"font-size:{tier:g}px;"
    if wrap_class == "gwrap full":  # contain-fit: fill BOTH viewport dimensions, never letterbox (v0.4.9)
        st += f"width:min(100%,calc(88vh*{vw:.4f}/{vh:.4f}));"
    GRAPH_FONT_W, GRAPH_LABEL_H, LABEL_CAP = _fw0, _lh0, _cap0
    svg = (f'<svg id="gsvg" style="{st}" viewBox="{vx:.0f} {vy:.0f} {vw:.0f} {vh:.0f}" xmlns="http://www.w3.org/2000/svg">'
           f'{defs}{"".join(lines)}{"".join(circles)}</svg>')
    # v0.4.3 (4): a colour legend written in words ("blue entity") is a lazy legend — it
    # shows real swatches, sourced from the same GRAPH_COLORS the dots are painted with,
    # plus line samples for one-way (arrowed) vs mutual, so nothing can drift apart. (Simo)
    # v0.4.5 (Simo: the "63 nodes · 121 edges" prose strip was still lazy): proper
    # viewer pattern — a floating legend panel OVER the canvas (pointer-events:none,
    # never blocks panning), counts baked INTO the swatch rows instead of a
    # sentence above the graph, zero-count categories hidden, buttons in the
    # opposite corner. Everything derived from the same data that draws the dots.
    sw = lambda c: f'<i class="sw" style="background:{c}"></i>'
    from collections import Counter
    counts = Counter(_node_colour(nd["path"]) for nd in nodes)
    resolved = [(f_, t_) for f_, t_ in g["edges"] if f_ in pos and t_ in pos]
    oneway = sum(1 for f_, t_ in resolved if (t_, f_) not in eset)
    mutual = sum(1 for f_, t_ in resolved if (t_, f_) in eset) // 2
    rows = ""
    for label, col in (("entity", GRAPH_COLORS["entities"]), ("concept", GRAPH_COLORS["concepts"]),
                       ("note", GRAPH_COLORS["queries"]), ("other", GRAPH_COLORS["other"])):
        n = counts.get(col, 0)
        if n:
            rows += f'{sw(col)}{label} <span class="muted">{n}</span><br>'
    rows += ('<svg width="20" height="10" class="lgs"><line x1="1" y1="5" x2="13" y2="5" stroke="#58626d"/>'
             f'<path d="M12,2 L19,5 L12,8 z" fill="#58626d"/></svg>one-way <span class="muted">{oneway}</span><br>')
    rows += f'<span class="ll"></span>mutual <span class="muted">{mutual}</span>'
    if broken:
        rows += f'<br><span class="llb"></span>broken <span class="muted">{len(broken)}</span>'
    rows += '<div class="muted" style="font-size:.7rem">scroll = zoom &middot; drag = pan &middot; F = fit</div>'
    return (f'<div class="{wrap_class}">{GRAPH_NAV}'
            f'<div class="glegend">{rows}</div>{svg}</div>' + GRAPH_JS)


def page_graph(core, g):
    return shell("graph", '<h1>graph</h1>' + _graph_markup(g, wrap_class="gwrap full"))


def ego_block(core, rel: str) -> str:
    """v0.4.6 milestone (Simo: "when I open up one of the wiki pages, I want to see
    linked neighbours, in the same graph style and tooling as the whole graph, but
    from the page's perspective"): the page's 1-hop neighbourhood rendered by the
    very same _graph_markup the /graph page uses — same layout kernel, same pan/
    zoom/fit, same floating legend with counts — viewBox centred on THIS page."""
    node_path = rel.rsplit(".", 1)[0]
    try:
        g = core.graph()
    except Exception:
        return ""
    if all(n["path"] != node_path for n in g["nodes"]):
        return ""
    hard = [(f_, t_) for f_, t_ in g["edges"] if not str(t_).startswith("?")]
    nbr = {node_path}
    for f_, t_ in hard:
        if f_ == node_path:
            nbr.add(t_)
        elif t_ == node_path:
            nbr.add(f_)
    if len(nbr) < 2:
        return ""  # orphan page: a graph of one dot says nothing, skip the block
    sub = {"nodes": [n for n in g["nodes"] if n["path"] in nbr],
           "edges": [(f_, t_) for f_, t_ in hard if f_ in nbr and t_ in nbr]}
    return ('<div class="egowrap">' + _graph_markup(sub, center_path=node_path)
            + '<p class="muted" style="margin:6px 0 0">neighbourhood view &middot; '
            + f'click a node to open its page &middot; <a href="/graph">whole graph</a></p></div>')


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
