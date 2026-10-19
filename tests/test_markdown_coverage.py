"""Syntax-coverage test for views.py: render a page containing the full markdown
surface and assert each construct becomes correct HTML."""
import re, sys
sys.path.insert(0, "/opt/data/wiki-server")
import views

MD = """---
title: Syntax Kitchen Sink
type: query
---
# H1
## H2 **bold** ##
Setext H1
=
Setext H2
-

para one line1
line2 same para

hard break here  
next line after break

***bold italic***, **bold**, __also bold__, *em*, _also em_, ~~gone~~.

link: [docs](https://example.com/a_b) with title [t](https://e.com "Tip") and autolink <https://auto.example> and bare https://bare.example/x, end.

image: ![alt text](https://e.com/i.png "cap")

`code` and ``a ` b`` span. escape: \\*not em\\*.

- tight ul item1
- item2 **b** with `code`
  - nested child
    - grandchild
- [ ] open task
- [x] done task

1. first
2. second
   1. ordered nested

second ol:

4. start-at-four ol
5. five

> quote line1
> quote line2
>
> > nested quote

loose list:

- first loose item

- second loose item

| Left | Center | Right |
|:-----|:------:|------:|
| a | b | c |
| **d** | `e` | [f](https://x.y) |

```python
def hi():
    return "<code> & raw"
```

***

--- text after thematic breaks

<details><summary>folded</summary>
hidden content
</details>

wikilinks: [[strata]] and [[missing-page|custom label]].
"""

LM = {"strata": "/view?path=entities/strata.md", "entities/strata.md": "/view?path=entities/strata.md"}
html = views.md_to_html(MD, LM)
compact = re.sub(r"\s+", " ", html) if False else " ".join(html.split())

EXPECTED = [
    ("ATX h1", "<h1>H1</h1>"),
    ("ATX h2 + bold + trailing hashes", "<h2>H2 <strong>bold</strong></h2>"),
    ("setext h1", "<h1>Setext H1</h1>"),
    ("setext h2", "<h2>Setext H2</h2>"),
    ("soft break joins para", "<p>para one line1 line2 same para</p>"),
    ("hard break", "here<br>next line after break"),
    ("bold italic", "<strong><em>bold italic</em></strong>"),
    ("bold", "<strong>bold</strong>"),
    ("__bold__", "<strong>also bold</strong>"),
    ("*em*", "<em>em</em>"),
    ("_em_", "<em>also em</em>"),
    ("strikethrough", "<del>gone</del>"),
    ("md link", '<a href="https://example.com/a_b">docs</a>'),
    ("md link title", 'title="Tip"'),
    ("autolink", '<a href="https://auto.example">https://auto.example</a>'),
    ("bare url, comma stays outside", '<a href="https://bare.example/x">https://bare.example/x</a>,'),
    ("image", '<img src="https://e.com/i.png" alt="alt text" title="cap" loading="lazy">'),
    ("code span", "<code>code</code>"),
    ("double-backtick span", "<code>a ` b</code>"),
    ("escape", "*not em*"),
    ("tight ul", '<ul class="task-list"><li>tight ul item1</li>'),
    ("nested list", "<ul><li>nested child<ul><li>grandchild</li></ul></li></ul>"),
    ("task open", '<li class="task"><span class="box"></span> open task</li>'),
    ("task done", '<li class="task"><span class="box done"></span> done task</li>'),
    ("ordered list", "<ol><li>first</li>"),
    ("ordered nested", "<ol>"),
    ("start attr", '<ol start="4">'),
    ("blockquote", "<blockquote><p>quote line1 quote line2</p>"),
    ("nested quote", "<blockquote><p>nested quote</p></blockquote></blockquote>"),
    ("loose list paras", "<li><p>first loose item</p></li>"),
    ("table align", 'style="text-align:center"'),
    ("table right", 'style="text-align:right"'),
    ("table content", "<strong>d</strong>"),
    ("fenced lang", '<pre><code class="lang-python">'),
    ("fenced raw", "&lt;code&gt; &amp; raw"),
    ("thematic breaks", "<hr>"),
    ("details passthrough", "<details><summary>folded</summary>"),
    ("wikilink resolved", '<a href="/view?path=entities/strata.md">strata</a>'),
    ("wikilink alias+dead", '<a href="/view?q=missing-page" class="dead">custom label</a>'),
]

fails = 0
for name, needle in EXPECTED:
    ok = needle in html or needle in compact
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else "  :: " + repr(needle)))
    if not ok:
        fails += 1
        frag = needle.split("<")[0].strip() or needle[:12]
        j = html.find(frag)
        print("      near: " + repr(html[max(0, j - 60):j + 160] if j >= 0 else "<fragment absent>"))

import re
body_wo_code = re.sub(r"(?s)<pre>.*?</pre>", "", html)
body_wo_code = re.sub(r"(?s)<code>.*?</code>", "", body_wo_code)
for name, lit in [("leftover **", "**"), ("leftover ~~", "~~"), ("leftover [[", "[["),
                  ("leftover ```", "```"), ("leftover |header|", "|Left |")]:
    hit = lit in body_wo_code
    print(("ok   no " if not hit else "FAIL ") + name)
    if hit:
        fails += 1

open("/opt/data/cache/scratch/sink.html", "w").write(html)
print("\n%d failures" % fails)
sys.exit(1 if fails else 0)
