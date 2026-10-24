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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from views import GRAPH_COLORS, GRAPH_FONT_W, GRAPH_LABEL_H, page_graph  # noqa: E402

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
    for m in re.finditer(r'<text([^>]*)x="([-\d.]+)" y="[-\d.]+"><tspan>([^<]+)</tspan>', svg):
        end, x, t = "text-anchor" in m.group(1), float(m.group(2)), m.group(3)
        w = len(t) * GRAPH_FONT_W
        x0, x1 = (x - w, x) if end else (x, x + w)
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
    return [(int(a), int(b)) for a, b in re.findall(r'<circle cx="(\d+)" cy="(\d+)"', svg)]


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
    for blk in svg.split('<g class="node">')[1:]:
        m = re.search(r'<circle cx="(\d+)" cy="(\d+)".*?<tspan>([^<]+)</tspan>', blk, re.S)
        if m:
            out.append((int(m[1]), int(m[2]), m[3]))
    return out


def main():
    g = build()
    svg = page_graph(None, g)
    pts = circles(svg)
    assert len(pts) == len(g["nodes"]), f"{len(pts)} circles for {len(g['nodes'])} nodes"
    Wc, Hc = canvas(svg)
    assert Wc >= 1000 and Hc >= 720, "canvas must never be smaller than the base"
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
    assert svg.count('<g class="node"><a href="/view?path=') == len(pts), "dots are not clickable"
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
    dgsvg = re.search(r'<svg viewBox.*?</svg>', dgsvg, re.S).group(0)
    assert dgsvg.count("<line") == 3, "one line per edge entry"
    assert dgsvg.count('marker-end="url(#ah)"') == 1, "exactly one arrowhead (the one-way edge)"
    assert '<marker id="ah"' in dgsvg, "the arrow marker is undefined"
    gp = {s: (x, y) for x, y, s in labeled(dgsvg)}
    m = re.search(r'x1="(\d+)" y1="(\d+)" x2="(\d+)" y2="(\d+)" marker-end', dgsvg)
    x1, y1, x2, y2 = map(int, m.groups())
    assert (x1, y1) == gp["gamma"], "the arrow belongs on the one-way edge, at its source"
    assert abs(math.hypot(x2 - gp["alpha"][0], y2 - gp["alpha"][1]) - 8) <= 1, \
        "arrow tip must stop at the target dot's rim (r=6+2)"
    # v0.4.3 (4): the legend shows real swatches — a colour legend written in prose
    # ("blue entity") was "super lazy" (Simo). Swatch colours must equal the dot colours
    # (single GRAPH_COLORS source), and line samples must label one-way vs mutual.
    assert svg.count('class="sw"') == 4, "legend needs one swatch per node kind"
    assert "blue entity" not in svg and "yellow concept" not in svg, "prose colour legend is back"
    for c in GRAPH_COLORS.values():
        assert f"background:{c}" in svg, f"legend swatch {c} missing"
    used = set(re.findall(r'r="6" fill="(#[0-9a-f]{6})"><title>', svg))
    assert used and used <= set(GRAPH_COLORS.values()), "dot colours drifted from GRAPH_COLORS"
    assert 'class="ll"' in svg and ">one-way" in svg and ">mutual" in svg, "line samples missing"
    print(f"graph layout OK: {len(pts)} nodes spread, {border} on border, {bad}/{tot} label overlaps, "
          f"growth 200 nodes: H={Hb:.0f} bad={badb}/{totb}")


if __name__ == "__main__":
    main()
