#!/usr/bin/env python3
"""Build a browsable list of every in-scope paper, for a human scope review.

The point is scanning: 500+ titles grouped so that anything mis-scoped stands
out. Each row shows the title, year, journal, record class, and the criterion it
was admitted under, with a link to the DOI and a tick box that persists.

  uv run scripts/build_review_list.py
"""

from __future__ import annotations

import collections
import glob
import html
import json
import sys
from pathlib import Path

from common import load_screening, DATA, read_json


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""), quote=True)


def main() -> int:
    man = read_json(DATA / "manifest.json", {}) or {}
    papers = man.get("papers", {})
    if not papers:
        sys.exit("No manifest. Run rename.py first.")

    verdicts = load_screening()

    rows = []
    for ck, p in papers.items():
        v = verdicts.get(p.get("doi"), {})
        rows.append({
            "ck": ck, "doi": p.get("doi"), "title": p.get("title") or "(no title)",
            "year": p.get("year") or 0, "journal": p.get("journal") or "",
            "cls": p.get("record_class") or v.get("record_class") or "?",
            "crit": v.get("criterion") or "?", "why": v.get("reason") or "",
            "preprint": bool(p.get("preprint")),
        })
    rows.sort(key=lambda r: (-r["year"], r["title"].lower()))

    by_year = collections.Counter(r["year"] for r in rows)
    by_cls = collections.Counter(r["cls"] for r in rows)

    out = [f"""<!doctype html>
<meta charset="utf-8"><title>TUS archive — {len(rows)} in-scope papers</title>
<style>
 :root {{ --bg:#fff; --fg:#17171a; --mut:#6b6b73; --line:#e6e6ea; --acc:#1d4ed8;
          --chip:#f1f5f9; --flag:#fef2f2; --flagfg:#b91c1c; }}
 @media (prefers-color-scheme:dark) {{ :root {{ --bg:#0f1012; --fg:#e9e9ec; --mut:#93939c;
   --line:#26272c; --acc:#93b4ff; --chip:#1c2028; --flag:#2a1417; --flagfg:#fca5a5; }} }}
 body {{ background:var(--bg); color:var(--fg); margin:0 auto; padding:1.5rem 1.25rem 5rem;
   max-width:74rem; font:14.5px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
 h1 {{ font-size:1.3rem; margin:0 0 .2rem; }} h2 {{ font-size:.95rem; margin:2rem 0 .4rem;
   position:sticky; top:0; background:var(--bg); padding:.5rem 0 .3rem; border-bottom:1px solid var(--line); }}
 .sub {{ color:var(--mut); font-size:.85rem; margin:0 0 1rem; }}
 .bar {{ position:sticky; top:0; z-index:9; background:var(--bg); padding:.6rem 0;
   border-bottom:1px solid var(--line); display:flex; gap:.5rem; flex-wrap:wrap; align-items:center; }}
 input[type=search] {{ flex:1; min-width:14rem; padding:.45rem .6rem; font-size:.9rem;
   border:1px solid var(--line); border-radius:6px; background:var(--bg); color:var(--fg); }}
 ol {{ list-style:none; padding:0; margin:0; }}
 li {{ display:grid; grid-template-columns:1.4rem 1fr; gap:.55rem; padding:.45rem .3rem;
   border-bottom:1px solid var(--line); align-items:start; }}
 li.flagged {{ background:var(--flag); }}
 a {{ color:var(--acc); text-decoration:none; font-weight:500; }} a:hover {{ text-decoration:underline; }}
 .meta {{ color:var(--mut); font-size:.78rem; margin-top:.12rem; }}
 .chip {{ display:inline-block; background:var(--chip); border-radius:3px; padding:.05rem .35rem;
   margin-right:.35rem; font-size:.72rem; }}
 input[type=checkbox] {{ margin-top:.2rem; width:.95rem; height:.95rem; cursor:pointer; }}
 .note {{ background:var(--chip); padding:.7rem .9rem; border-radius:6px; font-size:.85rem; margin:.8rem 0 0; }}
 #count {{ color:var(--mut); font-size:.82rem; }}
</style>
<h1>TUS archive &mdash; {len(rows)} in-scope papers</h1>
<p class="sub">Sorted newest first. Tick anything whose title suggests it should <em>not</em> be
here; ticks persist in this browser. Each row shows the rule it was admitted under.</p>
<p class="note"><strong>What to look for:</strong> ablation or MRgFUS, BBB opening, drug delivery,
imaging-only studies, shockwave/TPS, engineered nanoparticles, or broad reviews where TUS is one
section. Those should all have been excluded &mdash; if you see one, tick it and I will re-screen
against the methods.</p>
<div class="bar">
  <input type="search" id="q" placeholder="filter by title, journal, year, class or rule…">
  <span id="count"></span>
  <button id="showflags">show ticked only</button>
  <button id="copy">copy ticked DOIs</button>
</div>
"""]
    out.append('<p class="sub">' + " &middot; ".join(
        f"{n} {esc(c)}" for c, n in by_cls.most_common()) + "</p>")

    cur = None
    for r in rows:
        if r["year"] != cur:
            if cur is not None:
                out.append("</ol>")
            cur = r["year"]
            out.append(f'<h2>{cur or "no year"} &mdash; {by_year[cur]} papers</h2><ol>')
        bits = [f'<span class="chip">{esc(r["crit"])}</span>']
        if r["cls"] != "primary_study":
            bits.append(f'<span class="chip">{esc(r["cls"].replace("_", " "))}</span>')
        if r["journal"]:
            bits.append(f"<em>{esc(r['journal'])}</em>")
        if r["preprint"]:
            bits.append("preprint merged")
        out.append(
            f'<li data-doi="{esc(r["doi"])}" data-s="{esc((r["title"] + " " + r["journal"] + " " + r["cls"] + " " + r["crit"] + " " + str(r["year"])).lower())}">'
            f'<input type="checkbox"><span>'
            f'<a href="https://doi.org/{esc(r["doi"])}" target="_blank" rel="noopener">{esc(r["title"])}</a>'
            f'<div class="meta">{" ".join(bits)}</div></span></li>')
    out.append("</ol>")

    out.append("""
<script>
 const KEY="tus-review-flags"; let flags={};
 try{flags=JSON.parse(localStorage.getItem(KEY)||"{}")}catch(e){}
 const items=[...document.querySelectorAll("li[data-doi]")];
 const count=document.getElementById("count");
 function refresh(){
   const n=items.filter(li=>li.querySelector("input").checked).length;
   count.textContent=n?`${n} ticked`:`${items.length} papers`;
 }
 for(const li of items){
   const doi=li.dataset.doi, box=li.querySelector("input");
   if(flags[doi]){box.checked=true; li.classList.add("flagged");}
   box.addEventListener("change",()=>{
     li.classList.toggle("flagged",box.checked);
     if(box.checked) flags[doi]=true; else delete flags[doi];
     try{localStorage.setItem(KEY,JSON.stringify(flags))}catch(e){}
     refresh();
   });
 }
 document.getElementById("q").addEventListener("input",e=>{
   const q=e.target.value.toLowerCase().trim();
   for(const li of items) li.style.display = !q || li.dataset.s.includes(q) ? "" : "none";
 });
 let only=false;
 document.getElementById("showflags").addEventListener("click",()=>{
   only=!only;
   for(const li of items) li.style.display = !only || li.querySelector("input").checked ? "" : "none";
 });
 document.getElementById("copy").addEventListener("click",()=>{
   const d=items.filter(li=>li.querySelector("input").checked).map(li=>li.dataset.doi).join("\\n");
   navigator.clipboard.writeText(d); alert(d?`Copied ${d.split("\\n").length} DOIs`:"Nothing ticked");
 });
 refresh();
</script>""")

    (DATA / "review-list.html").write_text("\n".join(out), encoding="utf-8")
    print(f"wrote data/review-list.html ({len(rows)} papers)")
    for c, n in by_cls.most_common():
        print(f"   {n:4d}  {c}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
