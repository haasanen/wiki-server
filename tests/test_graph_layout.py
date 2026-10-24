"""Graph layout regression test (v0.4.2).

The original page_graph spring loop pulled with an uncapped quadratic term
and no damping: with real-size graphs every node overshot and jammed onto a
corner of the clamp rectangle, so the SVG rendered as ~4 blobs and an X of
overlapped edges. Fruchterman-Reingold with step caps + cooling must keep
nodes spread and off the border.
"""
import math
import os
import pathlib
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from views import GRAPH_COLORS, GRAPH_FONT_W, GRAPH_LABEL_H, page_graph  # noqa: E402
import views as views_mod  # noqa: E402

W, Hh = 1000, 720  # canvas BEFORE the layout grows it (v0.4.3(6): canvas fits content)


def canvas(svg):
    m = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', svg)
    return float(m.group(1)), float(m.group(2))


def lab_over(entries):
    """exact right-hanging label box overlaps (text x is the box left edge; a flipped
    label cannot exist once the canvas grows to fit — proven by edge_clip == 0)"""
    n = 0
    for i in range(len(entries)):
        for j in range(i + 1, len(entries)):
            x1, y1, t1 = entries[i]
            x2, y2, t2 = entries[j]
            if abs(y1 - y2) < GRAPH_LABEL_H:
                w1 = len(t1) * GRAPH_FONT_W
                w2 = len(t2) * GRAPH_FONT_W
                if not (x1 + w1 <= x2 + 2 or x2 + w2 <= x1 + 2):
                    n += 1
    return n


def edge_clip(svg):
    """labels crossing the canvas edge — with flip + grown canvas this must be zero"""
    Wc, _ = canvas(svg)
    n = 0
    for blk in svg.split('<g class="node" data-i')[1:]:
        m = re.search(r'translate\(([-\d.]+),([-\d.]+)\)', blk)
        t = re.search(r'<text([^>]*)x="([-\d.]+)"[^>]*><tspan>([^<]+)</tspan>', blk)
        if m and t:
            x = float(m.group(1)) + float(t.group(2))
            w = len(t.group(3)) * GRAPH_FONT_W
            x0, x1 = (x - w, x) if "text-anchor" in t.group(1) else (x, x + w)
            if x0 < 0 or x1 > Wc:
                n += 1
    return n


def build(n=40):
    nodes = [{"path": f"entities/n{i:02d}", "title": f"N{i}"} for i in range(n)]
    edges = [[nodes[i]["path"], nodes[(i + 1) % n]["path"]] for i in range(n)]
    edges += [[nodes[i]["path"], nodes[(i + 11) % n]["path"]] for i in range(0, n, 3)]
    for hub in (nodes[0]["path"], nodes[7]["path"]):  # two dense hubs
        edges += [[nd["path"], hub] for nd in nodes if nd["path"] != hub]
    return {"nodes": nodes, "edges": edges}


def circles(svg):
    return [(float(a), float(b)) for a, b in
            re.findall(r'<g class="node" data-i="\d+" transform="translate\(([-\d.]+),([-\d.]+)\)"', svg)]


def long_stem_build(n=30):
    """The failure Simo screenshotted: sibling raw-notes with long shared stems."""
    words = ("infrastructure architecture sandbox runbook diagnostics maintainability "
             "checkpoints").split()
    nodes = [{"path": f"queries/note-{words[i % len(words)]}-{words[(i * 3) % len(words)]}-{i:02d}",
              "title": f"N{i}"} for i in range(n)]
    edges = [[nodes[i]["path"], nodes[(i + 1) % n]["path"]] for i in range(n)]
    hub = nodes[0]["path"]
    edges += [[nd["path"], hub] for nd in nodes[1:]]   # one dense cluster
    return {"nodes": nodes, "edges": edges}


def labeled(svg):
    out = []
    for blk in svg.split('<g class="node" data-i')[1:]:
        m = re.search(r'translate\(([-\d.]+),([-\d.]+)\).*?<text([^>]*)x="([-\d.]+)"[^>]*><tspan>([^<]+)</tspan>', blk, re.S)
        if m:
            # label coords are absolute: group translate + group-relative text offset
            out.append((float(m.group(1)) + float(m.group(4)), float(m.group(2)) + 3, m.group(5)))
    return out


def main():
    g = build()
    svg = page_graph(None, g)
    pts = circles(svg)
    assert len(pts) == len(g["nodes"]), f"{len(pts)} circles for {len(g['nodes'])} nodes"
    Wc, Hc = canvas(svg)
    # v0.4.5 (Simo: "the graph does not fill the screen — the page sits in the
    # middle"): the old contract "never smaller than 1000x720" was the bug — a
    # canvas much bigger than its content renders the graph as a small island
    # letterboxed by dead margin. New contract: canvas hugs content (it may now
    # shrink as well as grow); allowed slack = one label width + 60px margin.
    xs = [x for x, y in pts]
    ys = [y for x, y in pts]
    widest = max(len(Path(nd["path"]).stem) for nd in g["nodes"]) * GRAPH_FONT_W
    assert Wc <= (max(xs) - min(xs)) + widest + 130 and \
        Hc <= (max(ys) - min(ys)) + GRAPH_LABEL_H + 130, \
        f"canvas {Wc:.0f}x{Hc:.0f} has dead space around content " \
        f"{max(xs) - min(xs):.0f}x{max(ys) - min(ys):.0f}"
    border = sum(1 for x, y in pts if x <= 31 or y <= 27 or y >= Hc - 21)
    assert border / len(pts) < 0.30, f"{border}/{len(pts)} nodes pinned at a canvas edge"
    assert edge_clip(svg) == 0, "labels cross the canvas edge"
    close = sum(1 for i in range(len(pts)) for j in range(i + 1, len(pts))
                if math.hypot(pts[i][0] - pts[j][0], pts[i][1] - pts[j][1]) < 30)
    total = len(pts) * (len(pts) - 1) // 2
    # v0.4.3 (6): explicit minimum-distance floor (34px) — FR alone left 48/1953 real-graph
    # pairs under 60px, min 19px; that is what "nodes still overlapping" looked like. (Simo)
    assert close == 0, f"{close}/{total} node pairs closer than 30px"
    # determinism: same input, same layout
    assert circles(page_graph(None, build())) == pts, "layout not deterministic"
    # v0.4.3: labels (9px text right of the dot) must not print on top of each other
    gl = labeled(page_graph(None, long_stem_build()))
    assert len(gl) == 30, f"{len(gl)} labelled nodes, expected 30"
    bad = lab_over(gl)
    tot = len(gl) * (len(gl) - 1) // 2
    assert bad / tot < 0.01, f"{bad}/{tot} label boxes still overlap"
    # v0.4.3: the whole node — dot AND label — must be a real link. The old markup had
    # the cursor promise on the dot (which did nothing) and pointer-events:none on the
    # label (which killed the only link): nodes looked clickable, were dead. (Simo, 2026-10-24)
    assert svg.count('<g class="node" data-i=') == len(pts), "dots are not clickable"
    # v0.4.4 (Simo: "proper intuitive navigation controls"): buttons + inline JS, and a
    # hard zero-dependency guarantee — the graph page may reference NO external asset.
    assert 'id="gsvg"' in svg and '<div class="gnav">' in svg and svg.count("<button") == 3, \
        "graph navigation controls missing"
    assert "<script>" in svg and "<script src=" not in svg, "interaction JS must be inline"
    assert "http://" not in svg.replace('xmlns="http://www.w3.org/2000/svg"', ""), \
        "graph page references an external resource (dependency creep)"
    assert svg.count('data-i="') == len(pts), "nodes lack drag identity"
    assert svg.count('data-f="') == len(g["edges"]), "edges lack drag rewiring data"
    # v0.4.4 (Simo: "we want the very minimum edge overlapping"): the greedy
    # crossing-reduction pass must be PROVEN to help on identical input — flag off vs on.
    def _cross(page):
        s = re.search(r'<svg[^>]*viewBox.*?</svg>', page, re.S).group(0)
        segs = [(int(m.group(1)), int(m.group(2)), tuple(map(float, m.groups()[2:])))
                for m in re.finditer(
                    r'<line data-f="(\d+)" data-t="(\d+)" class="edge[^"]*"'
                    r' x1="([-\d.]+)" y1="([-\d.]+)" x2="([-\d.]+)" y2="([-\d.]+)"', s)]
        n = 0
        for i in range(len(segs)):
            f1, t1, s1 = segs[i]
            for f2, t2, s2 in segs[i + 1:]:
                if len({f1, t1, f2, t2}) < 4:
                    continue
                if views_mod._segs_cross(s1[:2], s1[2:], s2[:2], s2[2:]):
                    n += 1
        return n
    views_mod.GRAPH_EDGE_PASS = False
    views_mod._LAYOUT_MEMO.clear()
    try:
        base_cross = _cross(page_graph(None, build()))
    finally:
        views_mod.GRAPH_EDGE_PASS = True
        views_mod._LAYOUT_MEMO.clear()
    after_cross = _cross(page_graph(None, build()))
    assert after_cross < base_cross, \
        f"edge-crossing pass changed nothing: {base_cross} -> {after_cross}"
    views_mod._LAYOUT_MEMO.clear()
    src = (pathlib.Path(__file__).resolve().parents[1] / "views.py").read_text(encoding="utf-8")
    assert ".node text{fill:var(--fg);font-size:9px;pointer-events:none}" not in src, \
        "the label click-killer is back"
    # v0.4.3: robustness to growth — the graph only gets bigger (Simo). At ~3x the live
    # size the canvas must grow and labels must still not print on each other.
    big = long_stem_build(200)
    svgb = page_graph(None, big)
    Wb, Hb = canvas(svgb)
    assert Wb > 1000 or Hb > 720, "canvas did not grow with the graph"
    ptsb = circles(svgb)
    closeb = sum(1 for i in range(len(ptsb)) for j in range(i + 1, len(ptsb))
                 if math.hypot(ptsb[i][0] - ptsb[j][0], ptsb[i][1] - ptsb[j][1]) < 30)
    totb2 = len(ptsb) * (len(ptsb) - 1) // 2
    assert closeb / totb2 < 0.01, f"growth: {closeb}/{totb2} dot pairs closer than 30px"
    assert edge_clip(svgb) == 0, "growth: labels cross the canvas edge"
    glb = labeled(svgb)
    assert len(glb) == 200, f"{len(glb)} labelled nodes in the growth test, expected 200"
    badb = lab_over(glb)
    totb = len(glb) * (len(glb) - 1) // 2
    assert badb / totb < 0.01, f"growth: {badb}/{totb} label boxes overlap at 200 nodes"
    # v0.4.3: directionality (Simo's spec) — one-way links get an arrowhead, mutual pairs
    # stay plain lines; the arrow tip must stop at the target dot's rim, not its centre.
    dg = {"nodes": [{"path": p, "title": ""} for p in
                    ("entities/alpha", "entities/beta", "entities/gamma")],
          "edges": [["entities/alpha", "entities/beta"], ["entities/beta", "entities/alpha"],
                    ["entities/gamma", "entities/alpha"]]}
    dgsvg = page_graph(None, dg)
    # scope to the graph svg itself: the legend legitimately adds its own sample line
    dgsvg = re.search(r'<svg[^>]*viewBox.*?</svg>', dgsvg, re.S).group(0)
    assert dgsvg.count("<line") == 3, "one line per edge entry"
    assert dgsvg.count('marker-end="url(#ah)"') == 1, "exactly one arrowhead (the one-way edge)"
    assert '<marker id="ah"' in dgsvg, "the arrow marker is undefined"
    # node markup order == nodes list order: alpha, beta, gamma
    gp = dict(zip(("alpha", "beta", "gamma"), circles(dgsvg)))
    m = re.search(r'x1="(\d+)" y1="(\d+)" x2="(\d+)" y2="(\d+)" marker-end', dgsvg)
    x1, y1, x2, y2 = map(int, m.groups())
    assert (x1, y1) == gp["gamma"], "the arrow belongs on the one-way edge, at its source"
    assert abs(math.hypot(x2 - gp["alpha"][0], y2 - gp["alpha"][1]) - 8) <= 1, \
        "arrow tip must stop at the target dot's rim (r=6+2)"
    # v0.4.3 (4): the legend shows real swatches — a colour legend written in prose
    # ("blue entity") was "super lazy" (Simo). Swatch colours must equal the dot colours
    # (single GRAPH_COLORS source), and line samples must label one-way vs mutual.
    kinds = {"entities": 0, "concepts": 0, "queries": 0, "other": 0}
    for nd in g["nodes"]:
        pth = nd["path"]
        kinds["entities" if pth.startswith("entities/") else
              "concepts" if pth.startswith("concepts/") else
              "queries" if pth.startswith("queries/") else "other"] += 1
    n_kinds = sum(1 for v in kinds.values() if v)
    assert svg.count('class="sw"') == n_kinds, \
        f"legend must show exactly the {n_kinds} non-empty node kinds"
    assert "blue entity" not in svg and "yellow concept" not in svg, "prose colour legend is back"
    rev = {v: k for k, v in zip(("entities", "concepts", "queries", "other"),
                                 GRAPH_COLORS.values())}
    for c in GRAPH_COLORS.values():
        if kinds[rev.get(c, "other")]:
            assert f"background:{c}" in svg, f"legend swatch {c} missing"
    # v0.4.5 (Simo: "why is 63 nodes · 121 edges still written lazily"): counts live
    # INSIDE the legend rows now — no prose stats strip above the graph.
    assert not re.search(r'\d+ nodes · \d+ edges', svg), "prose stats strip is back"
    assert re.search(r'one-way <span class="muted">\d+</span>', svg), "edge counts missing from legend"
    assert re.search(r'mutual <span class="muted">\d+</span>', svg), "mutual count missing"
    for kind, lab in (("entities", "entity"), ("concepts", "concept"),
                      ("queries", "raw-note"), ("other", "other")):
        present = f'{lab} <span class="muted">' in svg
        assert present == (kinds[kind] > 0), f"{lab} row present={present} but count={kinds[kind]}"
    used = set(re.findall(r'r="6" fill="(#[0-9a-f]{6})"><title>', svg))
    assert used and used <= set(GRAPH_COLORS.values()), "dot colours drifted from GRAPH_COLORS"
    assert 'class="ll"' in svg and ">one-way" in svg and ">mutual" in svg, "line samples missing"
    print(f"graph layout OK: {len(pts)} nodes spread, {border} on border, {bad}/{tot} label overlaps, "
          f"growth 200 nodes: H={Hb:.0f} bad={badb}/{totb}")


if __name__ == "__main__":
    main()
