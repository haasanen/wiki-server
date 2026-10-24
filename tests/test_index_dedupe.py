"""index.md maintenance regression test (v0.4.2).

update_index used to refresh only the FIRST matching line, so historical
duplicate entries survived every refresh, and its loose matcher
(`f"[[{stem}]]" in line`) could mis-hit lines of other pages that merely
cross-link the stem. Must match by line prefix and self-heal duplicates.
"""
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server  # noqa: E402

d = pathlib.Path(tempfile.mkdtemp())
server.WIKI = d
(d / "index.md").write_text(
    "# Wiki Index\n\n## Entities\n"
    "- [[strata]] — old summary\n- [[strata]] — old summary\n"
    "- [[note-x]] — see [[strata]] for the engine\n\n## Concepts\n",
    encoding="utf-8")
text = "---\ntitle: Strata\n---\n\nStrata is the local LLM engine. New summary.\n"

server.update_index("entities/strata.md", text)
out = (d / "index.md").read_text()
assert out.count("[[strata]] —") == 1, out
assert "[[note-x]]" in out, "cross-linking line must survive untouched"
assert "[[strata]] for the engine" in out, out
server.update_index("entities/strata.md", text, remove=True)
out = (d / "index.md").read_text()
assert "[[strata]] —" not in out and "[[note-x]]" in out, out
print("index dedupe + prefix-matching OK")
