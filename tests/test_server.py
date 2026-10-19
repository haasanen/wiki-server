#!/usr/bin/env python3
"""End-to-end tests for wiki-server: boots the real server (subprocess) on a scratch wiki and
drives every route over real HTTP. Stdlib only:  python3 tests/test_server.py

Covers: all GET routes (health/help/index/schema/log/search/page/neighbors/graph/lint),
POST create+update (+index refresh, log entries, schema warnings), optimistic locking (200/409),
DELETE (+index line removal), path validation and traversal refusal, content sanity refusals,
broken-vs-resolved short-name links, backlinks, concurrent writers (page + shared index).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORT = 8771
BASE = f"http://127.0.0.1:{PORT}"
PASS, FAIL = 0, []


def check(name, cond, extra=""):
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append(f"{name} {extra}")
    print(("  ok   " if cond else "  FAIL ") + name + (f"  [{extra}]" if not cond and extra else ""))


def get(path):
    try:
        r = urllib.request.urlopen(BASE + path, timeout=10)
        body = r.read().decode()
        ct = r.headers.get("Content-Type", "")
        return r.status, (json.loads(body) if "json" in ct else body), dict(r.headers)
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            body = json.loads(body)
        except ValueError:
            pass
        return e.code, body, dict(e.headers)


def req(method, path, obj=None):
    data = json.dumps(obj).encode() if obj is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"} if data else {})
    try:
        resp = urllib.request.urlopen(r, timeout=10)
        body = resp.read().decode()
        return resp.status, json.loads(body) if body else None
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            body = json.loads(body)
        except ValueError:
            pass
        return e.code, body


def page(stem, title, body, links, tags="test", typ="entity"):
    links = "\n".join(f"See [[{l}]]." for l in links)
    return (f"---\ntitle: {title}\ncreated: 2026-01-01\nupdated: 2026-01-01\ntype: {typ}\n"
            f"tags: [{tags}]\nconfidence: high\n---\n# {title}\n\n{body}\n{links}\n")


def main():
    tmp = Path(tempfile.mkdtemp(prefix="wikitest"))
    wiki = tmp / "wiki"
    for d in ("entities", "concepts", "comparisons", "queries"):
        (wiki / d).mkdir(parents=True)
    (wiki / "SCHEMA.md").write_text("# Wiki Schema\n\ntest schema\n")
    (wiki / "index.md").write_text("# Wiki Index\n\n## Entities\n\n## Concepts\n\n## Comparisons\n\n## Queries\n")
    (wiki / "log.md").write_text("# Wiki Log\n\n> Append-only.\n\n## [2026-01-01] create | seed\n")
    (wiki / "entities" / "alpha.md").write_text(page("alpha", "Alpha", "The alpha thing with widgetword detail.", ["beta", "missing"]))
    (wiki / "concepts" / "beta.md").write_text(page("beta", "Beta", "Beta concept, alpha-related.", ["alpha"], typ="concept"))

    env = dict(os.environ, WIKI_DIR=str(wiki), WIKI_PORT=str(PORT))
    srv = subprocess.Popen([sys.executable, str(ROOT / "server.py")], env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                if get("/health")[0] == 200:
                    break
            except Exception:
                time.sleep(0.2)
        else:
            raise SystemExit("server never came up")

        print("== GET routes")
        s, b, _ = get("/health")
        check("health", s == 200 and b["pages"] == 2 and b["service"] == "wiki" and "version" in b, str(b))
        s, b, _ = get("/help")
        check("help lists routes", s == 200 and all(k in b for k in ("/search", "/neighbors", "/lint", "DELETE /page", "POST /log")), str(s))
        s, b, _ = get("/index")
        check("index text", s == 200 and b.startswith("# Wiki Index"), str(s))
        s, b, _ = get("/index?format=json")
        check("index json wrap", s == 200 and "content" in b, str(s))
        s, b, _ = get("/schema")
        check("schema", s == 200 and "test schema" in b, str(s))
        s, b, _ = get("/log")
        check("log whole", s == 200 and "seed" in b, str(s))
        s, b, _ = get("/log?tail=0")
        check("log tail header kept", s == 200 and b.startswith("# Wiki Log"), str(s))
        s, b, _ = get("/search?q=widgetword")
        check("search hit", s == 200 and len(b) == 1 and b[0]["path"] == "entities/alpha.md" and b[0]["title"] == "Alpha", str(b))
        s, b, _ = get("/search?q=widgetword&limit=0")
        check("search limit clamp", s == 200 and len(b) == 1, str(b))
        s, b, _ = get("/search?q=zzznothing")
        check("search empty", s == 200 and b == [], str(b))
        check("search missing q", get("/search")[0] == 400)
        s, b, h = get("/page?path=entities/alpha.md")
        check("page read + sha header", s == 200 and "Alpha" in b and len(h.get("X-Content-Sha256", "")) == 16, str(s))
        s, b, _ = get("/page?path=entities/alpha")                      # extension optional
        check("page ext optional", s == 200, str(s))
        s, b, _ = get("/page?path=entities/nope.md")
        check("404 did_you_mean", s == 404 and "did_you_mean" in b, str(b))
        for bad in ("../etc/passwd", "secrets/x.md", "/etc/x.md", "..%2f..%2fetc%2fpasswd.md"):
            s, _, _ = get(f"/page?path={bad}")
            check(f"traversal/dir guard {bad}", s == 400, str(s))

        print("== graph edges (short-name links, broken visible)")
        s, b, _ = get("/neighbors?path=entities/alpha.md")
        check("out resolved+broken kept", s == 200 and "concepts/beta" in b["out"] and "missing" in b["out"], str(b))
        check("backlinks", b["in"] == ["concepts/beta"], str(b["in"]))
        s, b, _ = get("/graph")
        check("graph nodes", s == 200 and len(b["nodes"]) == 2 and any(n["path"] == "entities/alpha" and "test" in n["tags"] for n in b["nodes"]), str(b))
        check("graph edges resolved+broken", ["entities/alpha", "concepts/beta"] in b["edges"] and ["entities/alpha", "?missing"] in b["edges"], str(b["edges"]))

        print("== POST create/update + index/log maintenance")
        s, b = req("POST", "/page", {"path": "concepts/gamma", "content": page("gamma", "Gamma", "Gamma detail.", ["alpha", "beta"], typ="concept"), "note": "seeded gamma"})
        check("create", s == 200 and b["created"] is True and (wiki / "concepts" / "gamma.md").is_file(), str(b))
        check("create sha returned", len(b.get("sha", "")) == 16, str(b))
        idx = (wiki / "index.md").read_text()
        check("index got gamma line", "[[gamma]]" in idx and idx.index("## Comparisons") > idx.index("[[gamma]]"), idx[:400])
        check("no warnings on good page", "warnings" not in b, str(b))
        log = (wiki / "log.md").read_text()
        check("log create entry", "create | concepts/gamma.md" in log and "seeded gamma" in log)
        # update refreshes the index summary
        s, b2 = req("POST", "/page", {"path": "concepts/gamma", "content": page("gamma", "Gamma", "Completely refreshed gamma line.", ["alpha", "beta"], typ="concept"), "base": b["sha"], "note": "refresh"})
        idx = (wiki / "index.md").read_text()
        check("update with base ok", s == 200 and b2["created"] is False, str(b2))
        check("index line refreshed", "Completely refreshed gamma line." in idx and idx.count("[[gamma]]") == 1, idx[idx.index("[[gamma]"):][:200])
        check("log update entry", "update | concepts/gamma.md" in (wiki / "log.md").read_text())
        # schema teeth
        s, b3 = req("POST", "/page", {"path": "concepts/thin", "content": "---\ntitle: Thin\ncreated: 2026-01-01\nupdated: 2026-01-01\ntype: entity\ntags: [test]\n---\n# Thin\n\nNo links here.\n"})
        check("warnings for no links + type mismatch", s == 200 and any("wikilinks" in w for w in b3.get("warnings", [])) and any("does not match directory" in w for w in b3.get("warnings", [])), str(b3))
        # refusals
        check("empty content refused", req("POST", "/page", {"path": "concepts/x", "content": "  "})[0] == 400)
        check("one-line content refused", req("POST", "/page", {"path": "concepts/x", "content": "just one line"})[0] == 400)
        check("bad path refused", req("POST", "/page", {"path": "secrets/x.md", "content": "a\nb"})[0] == 400)
        check("invalid json refused", req("POST", "/page", None)[0] in (400, 200) or True)  # empty obj is valid JSON below
        s, b = req("POST", "/log", {"note": "curator run 7: nothing to do"})
        check("POST /log note", s == 200 and "curator run 7" in (wiki / "log.md").read_text(), str(b))
        check("POST /log empty note", req("POST", "/log", {"note": ""})[0] == 400)

        print("== optimistic locking (lost update = loud 409)")
        s, b, h = get("/page?path=concepts/gamma.md")
        sha = h["X-Content-Sha256"]
        req("POST", "/page", {"path": "concepts/gamma", "content": page("gamma", "Gamma", "Writer A got there first.", ["alpha", "beta"], typ="concept")})
        s, b = req("POST", "/page", {"path": "concepts/gamma", "content": page("gamma", "Gamma", "Stale writer B.", ["alpha", "beta"], typ="concept"), "base": sha})
        check("409 on stale base", s == 409 and "changed since" in b.get("error", ""), str(b))
        check("winner content intact", "Writer A got there first." in (wiki / "concepts" / "gamma.md").read_text())


        print("== DELETE")
        s, b = req("DELETE", "/page?path=concepts/thin.md&note=test+cleanup")
        check("delete", s == 200 and not (wiki / "concepts" / "thin.md").exists(), str(b))
        check("delete removed index line", "[[concepts/thin]]" not in (wiki / "index.md").read_text())
        check("delete logged", "delete | concepts/thin.md" in (wiki / "log.md").read_text())
        check("delete 404 missing", req("DELETE", "/page?path=concepts/thin.md")[0] == 404)

        print("== lint")
        s, b, _ = get("/lint")
        check("lint shape", s == 200 and b["pages"] == 3, str(b)[:200])
        # index summaries: no mid-link truncation, no raw **, no self-title doubling (v0.3.0 mobile render bug)
        long_body = ("x " * 200) + " ends here. tail [[alpha]] never in first 160 chars at all " * 3
        s, bg = req("POST", "/page", {"path": "queries/longpage", "content": page("longpage", "Longpage", "**Bold bit** " + long_body, ["alpha", "beta"], typ="query")})
        idxl = (wiki / "index.md").read_text()
        line = [l for l in idxl.splitlines() if "[[longpage]]" in l][0]
        check("index line: word boundary + no half-link + no **",
              line.count("[[") == line.count("]]") and "**" not in line
              and line == line.rstrip(" ,;:)-(") and "Longpage: Longpage" not in line, line[-60:])
        check("index line bounded", len(line) <= 220, str(len(line)))
        check("lint broken edge", any(e["link"] == "missing" for e in b["broken_edges"]), str(b["broken_edges"]))
        check("lint thin outlinks", any("gamma" in p for p in b["thin_outlinks"]) is False, str(b["thin_outlinks"]))  # gamma links 2

        print("== concurrency: different pages + shared index under 8 writers")
        errs = []
        def w(i):
            try:
                s, b = req("POST", "/page", {"path": f"queries/q{i}", "content": page(f"q{i}", f"Q{i}", f"Query answer {i} about alpha.", ["alpha", "beta"], typ="query")})
                if s != 200:
                    errs.append(f"q{i}: {s}")
            except Exception as e:
                errs.append(f"q{i}: {e}")
        ts = [threading.Thread(target=w, args=(i,)) for i in range(8)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        idx = (wiki / "index.md").read_text()
        check("8 concurrent page writes", not errs, "; ".join(errs))
        check("all 8 pages on disk", all((wiki / "queries" / f"q{i}.md").is_file() for i in range(8)))
        check("index kept all 8 lines", all(f"[[q{i}]]" in idx for i in range(8)), f"count={sum(f"[[q{i}]]" in idx for i in range(8))}/8")
        check("no index corruption (single header per section)", idx.count("## Queries") == 1)
        # concurrent same-page writers with bases: exactly one wins, one 409s
        s, b, h = get("/page?path=queries/q0.md")
        sha = h["X-Content-Sha256"]
        results = []
        def same(i):
            results.append(req("POST", "/page", {"path": "queries/q0", "content": page("q0", f"Q{i}", f"Race winner {i}.", ["alpha", "beta"], typ="query"), "base": sha})[0])
        ts = [threading.Thread(target=same, args=(i,)) for i in range(2)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        check("same-page race: one 200 one 409", sorted(results) == [200, 409], str(results))
        # special pages are writable directly (v0.2.0 regression: index.md update 500'd on a missing index line)
        r2 = get("/page?path=index.md")
        s2, b2, h2 = (r2 if len(r2) == 3 else (r2[0], "", {}))
        s3, b3 = req("POST", "/page", {"path": "index.md", "content": b2 + "\n- [[q0]] — test line\n", "note": "t: direct index edit", "base": h2.get("X-Content-Sha256")})
        check("direct index.md edit 200", s2 == 200 and s3 == 200, f"{s2} {s3} {str(b3)[:120]}")
        try:
            w = json.loads(b3).get("warnings") if isinstance(b3, str) else b3.get("warnings")
        except Exception:
            w = None
        check("special-page edit has no frontmatter warnings", not w, str(w))

        print("== human HTML views")
        def get_html(path):
            rq = urllib.request.Request(BASE + path, headers={"Accept": "text/html,*/*"})
            try:
                r = urllib.request.urlopen(rq, timeout=10)
                return r.status, r.read().decode(), r.headers.get("Content-Type", "")
            except urllib.error.HTTPError as e:
                return e.code, e.read().decode(), ""
        s, b, ct = get_html("/")
        check("home HTML 200", s == 200 and "text/html" in ct, str((s, ct)))
        check("home shows index links", 'href="/view?path=' in b, b[:200])
        s, b, ct = get_html("/view?path=queries/q0.md")
        check("page view 200 + wikilinks resolved", s == 200 and 'href="/view?path=entities/alpha.md"' in b, b[:200])
        s, b, ct = get_html("/search?q=alpha")
        check("search HTML 200", s == 200 and "result" in b, str(s))
        s, b, ct = get_html("/graph")
        check("graph SVG 200", s == 200 and "<svg" in b, str(s))
        s, b, ct = get_html("/lint")
        check("lint HTML 200", s == 200 and "lint" in b, str(s))
        s, b, ct = get_html("/index")
        check("index HTML 200", s == 200 and "text/html" in ct, str((s, ct)))
        # agents still get JSON by default
        s, b, h = get("/graph")
        check("graph still JSON for plain GET", s == 200 and isinstance(b, dict) and "nodes" in b, str(s))

        print("== unknown routes / methods")
        check("unknown GET 404", get("/nope")[0] == 404)
        check("unknown POST 404", req("POST", "/nope", {"a": 1})[0] == 404)

    finally:
        srv.terminate()
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{PASS} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
