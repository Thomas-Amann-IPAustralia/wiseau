"""Which pages would a new guard counting rule flip, and is the new choice better? (ADR-042)

Reads guard_cache.py's output. For each page where the raw count and the
plausible-word count pick different texts, scores both texts against the page's
blind reference if it has one, else against its own hidden text layer (a silver
reference, as silver_layer.py), else prints the counts only (check those by eye).

  python benchmarks/bakeoff/guard_eval.py OUT.json
"""
import json, re, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent.parent; sys.path.insert(0,str(ROOT/"benchmarks/bakeoff"))
from score import score_page
import pymupdf
from regions import DOCS
c=json.load(open(sys.argv[1]))
W=re.compile(r"[^\W_]+")
sys.path.insert(0,str(ROOT/"backend"))
from parsers.tesseract_layout import plausible_word_count as plaus, word_count as raw  # the shipped rule
rules={"raw":raw,"plaus":plaus}
refs=ROOT/"benchmarks/bakeoff/references"
def layer(key):
    k,n=key.rsplit("_p",1); return pymupdf.open(ROOT/DOCS[k])[int(n)-1].get_text()
for key,v in sorted(c.items()):
    if v["psm3"] is None: print(key,"psm3 None"); continue
    picks={r:("psm3" if f(v["psm3"])>=0.85*f(v["guard"]) else "guard") for r,f in rules.items()}
    if picks["raw"]!=picks["plaus"]:
        ref=refs/f"{key}.txt"
        src="ref" if ref.exists() else "silver"
        rt=ref.read_text() if ref.exists() else layer(key)
        if len(W.findall(rt))<50:
            print(f"FLIP {key}: raw->{picks['raw']} plaus->{picks['plaus']}  [no reference] counts raw {raw(v['guard'])}/{raw(v['psm3'])} plaus {plaus(v['guard'])}/{plaus(v['psm3'])}"); continue
        sg=score_page(rt,v["guard"])["wer"]; sp=score_page(rt,v["psm3"])["wer"]
        print(f"FLIP {key}: raw->{picks['raw']} plaus->{picks['plaus']}  [{src}] wer guard {sg:.3f} psm3 {sp:.3f}  counts raw {raw(v['guard'])}/{raw(v['psm3'])} plaus {plaus(v['guard'])}/{plaus(v['psm3'])}")
print("pages",len(c))
