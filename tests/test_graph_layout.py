"""Graph layout regression test (v0.4.2).

The original page_graph spring loop pulled with an uncapped quadratic term
and no damping: with real-size graphs every node overshot and jammed onto a
corner of the clamp rectangle, so the SVG rendered as ~4 blobs and an X of
overlapped edges. Fruchterman-Reingold with step caps + cooling must keep
nodes spread and off the border.
"""
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from views import page_graph  # noqa: E402

W, Hh = 1000, 720  # canvas used by page_graph


def build(n=40):
    nodes = [{"path": f"entities/n{i:02d}", "title": f"N{i}"} for i in range(n)]
    edges = [[nodes[i]["path"], nodes[(i + 1) % n]["path"]] for i in range(n)]
    edges += [[nodes[i]["path"], nodes[(i + 11) % n]["path"]] for i in range(0, n, 3)]
    for hub in (nodes[0]["path"], nodes[7]["path"]):  # two dense hubs
        edges += [[nd["path"], hub] for nd in nodes if nd["path"] != hub]
    return {"nodes": nodes, "edges": edges}


def circles(svg):
    return [(int(a), int(b)) for a, b in re.findall(r'<circle cx="(\d+)" cy="(\d+)"', svg)]


def main():
    g = build()
    svg = page_graph(None, g)
    pts = circles(svg)
    assert len(pts) == len(g["nodes"]), f"{len(pts)} circles for {len(g['nodes'])} nodes"
    border = sum(1 for x, y in pts if x <= 41 or x >= W - 40 or y <= 41 or y >= Hh - 29)
    assert border / len(pts) < 0.30, f"{border}/{len(pts)} nodes stuck on the clamp border"
    close = sum(1 for i in range(len(pts)) for j in range(i + 1, len(pts))
                if math.hypot(pts[i][0] - pts[j][0], pts[i][1] - pts[j][1]) < 20)
    total = len(pts) * (len(pts) - 1) // 2
    assert close / total < 0.10, f"{close}/{total} node pairs closer than 20px"
    # determinism: same input, same layout
    assert circles(page_graph(None, build())) == pts, "layout not deterministic"
    print(f"graph layout OK: {len(pts)} nodes spread, {border} on border")


if __name__ == "__main__":
    main()
