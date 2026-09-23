"""Build the submission page: fills in the latest benchmark table and your links.

  python scripts/build_submission.py --repo https://github.com/you/firstcall \
      --customer "Jane Doe, platform lead at Acme (design partner)" \
      --quote "Every incident starts with the same 15 minutes of digging." \
      --demo https://youtu.be/...
Writes docs/submission.html (one self-contained file: open it, or deploy the docs/ folder).
"""
import argparse
import glob
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ap = argparse.ArgumentParser()
ap.add_argument("--repo", default="https://github.com/adam-bouafia/firstcall")
ap.add_argument("--demo", default="#demo")
ap.add_argument("--customer", default="TO FILL: name, role, company (ask their permission on the day)")
ap.add_argument("--quote", default="TO FILL: one sentence from them about the problem")
ap.add_argument("--validation", action="append", default=[],
                help='repeatable: "Name, role at Company | one sentence they said". '
                     'Each becomes a quote card in the Customer section.')
ap.add_argument("--ttd", default="")  # e.g. "~5 s"
ap.add_argument("--cost", default="")
ap.add_argument("--bench", default="", help="path to a bench results .md (default: the newest)")
a = ap.parse_args()

tpl = (ROOT / "docs" / "submission.template.html").read_text()

# newest benchmark table -> HTML
md = a.bench or (sorted(glob.glob(str(ROOT / "bench" / "results" / "*.md")))[-1]
                 if glob.glob(str(ROOT / "bench" / "results" / "*.md")) else "")
if md:
    rows = [ln for ln in Path(md).read_text().splitlines() if ln.startswith("|")]
    cells = [[c.strip() for c in ln.strip("|").split("|")] for ln in rows if not set(ln) <= set("|-: ")]
    html = ["<table><thead><tr>" + "".join(f"<th>{c}</th>" for c in cells[0]) + "</tr></thead><tbody>"]
    for row in cells[1:]:
        html.append("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>")
    html.append("</tbody></table>")
    table = "\n".join(html)
    note = [ln for ln in Path(md).read_text().splitlines() if ln.startswith("*score*")]
    if note:
        table += f'<p class="label" style="padding:10px 12px">{note[0].replace("*", "")}</p>'
else:
    table = '<table><tbody><tr><td>Run <code>make bench PROVIDER=nebius</code>, then rebuild this page.</td></tr></tbody></table>'

# customer-discovery quotes collected on the day (docs/discovery-script.md)
cards = []
for v in a.validation:
    who, _, said = v.partition("|")
    cards.append('<div class="card"><h3>Validation</h3>'
                 f'<blockquote>"{said.strip()}"</blockquote>'
                 f'<p><span class="fill">{who.strip()}</span></p></div>')
validation = "\n    ".join(cards)

arch = re.search(r'<svg viewBox="0 0 980 400".*?</svg>', (ROOT / "docs" / "board.html").read_text(), re.S)
out = (tpl.replace("{{BENCH_TABLE}}", table).replace("{{VALIDATION}}", validation)
          .replace("{{ARCH_SVG}}", arch.group(0) if arch else "")
          .replace("{{REPO}}", a.repo).replace("{{DEMO}}", a.demo)
          .replace("{{CUSTOMER}}", a.customer).replace("{{CUSTOMER_QUOTE}}", a.quote)
          .replace("{{TTD}}", a.ttd or "~5 s")
          .replace("{{COST}}", a.cost or "$0.00024"))
(ROOT / "docs" / "submission.html").write_text(out)

# the pitch deck, from the same facts (required by the submission form: a PUBLIC link)
vslide = "".join(f'<blockquote>"{v.partition("|")[2].strip()}"</blockquote>'
                 f'<p class="muted"><span class="fill">{v.partition("|")[0].strip()}</span></p>'
                 for v in a.validation)
deck = ((ROOT / "docs" / "slides.template.html").read_text()
        .replace("{{BENCH_TABLE}}", table).replace("{{ARCH_SVG}}", arch.group(0) if arch else "")
        .replace("{{REPO}}", a.repo).replace("{{DEMO}}", a.demo)
        .replace("{{CUSTOMER}}", a.customer).replace("{{CUSTOMER_QUOTE}}", a.quote)
        .replace("{{VALIDATION_SLIDE}}", vslide)
        .replace("{{TTD}}", a.ttd or "~5 s").replace("{{COST}}", a.cost or "$0.00024"))
(ROOT / "docs" / "slides.html").write_text(deck)

# a clean public folder: docs/ also holds pitch notes, the day plan and the judge mapping,
# which should not be one URL guess away from a judge.
site = ROOT / "docs" / "site"
site.mkdir(exist_ok=True)
(site / "index.html").write_text(out)                 # landing page = the submission page
(site / "slides.html").write_text(deck)
(site / "board.html").write_text((ROOT / "docs" / "board.html").read_text())

print(f"wrote docs/submission.html ({len(a.validation)} validation quote(s))")
print("wrote docs/slides.html      (arrows / click to advance, F for fullscreen, Ctrl-P -> PDF)")
print()
print("wrote docs/site/        (index.html + slides.html + board.html - nothing else)")
print()
print("deploy:  npx vercel deploy --prod docs/site")
print("  live product link  ->  <your-vercel-url>/")
print("  pitch slides link  ->  <your-vercel-url>/slides.html")
