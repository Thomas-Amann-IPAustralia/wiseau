"""Search per-page preprocessing rules on development pages, then test them held out (roadmap 14 item 1.2).

A rule applies one variant (pre-deskew, pre-norm, pre-sauvola, pre-denoise) only
to pages where one feature (survey/preprocess-features.json) passes a threshold;
every other page keeps the original (pre-ident). Thresholds are every value a
development page has. Rules are ranked by the mean wer change over the development
sets (A scans, B, C, D, L); the best eight are then scored on the held-out sets
(E, F). Needs score.py's results.json.

  python benchmarks/bakeoff/rule_search.py
"""
import json, sys, itertools
sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent))
import os; os.chdir(os.path.dirname(os.path.abspath(__file__)))
from compare import keys
r=json.load(open("results.json")); f=json.load(open("survey/preprocess-features.json"))
dev=sum((keys(s,False) for s in "abcdl"),[]); held=sum((keys(s,False) for s in "ef"),[])
base=r["pre-ident"]
feats={"abs_skew":lambda k:abs(f[k]["skew_deg"]),"specks":lambda k:f[k]["specks_per_mpx"],
       "contrast":lambda k:f[k]["contrast"],"blur":lambda k:f[k]["blur_lapvar"],"dpi":lambda k:f[k]["native_dpi"],
       "greyish":lambda k:0 if f[k]["type"] in ("bilevel","mixed") else 1, "has_photo":lambda k:1 if f[k]["type"]=="mixed" else 0}
best=[]
for v in ["pre-deskew","pre-norm","pre-sauvola","pre-denoise"]:
    res=r[v]
    for name,fn in feats.items():
        vals=sorted({fn(k) for k in dev})
        for t in vals:
            for side in (">=","<="):
                sel=[k for k in dev if (fn(k)>=t if side==">=" else fn(k)<=t)]
                if len(sel)<3: continue
                d=sum(res[k]["wer"]-base[k]["wer"] for k in sel)/len(dev)
                best.append((d,v,name,side,t,len(sel)))
best.sort()
for d,v,name,side,t,n in best[:8]:
    fn=feats[name]; sel=[k for k in held if (fn(k)>=t if side==">=" else fn(k)<=t)]
    dh=sum(r[v][k]["wer"]-base[k]["wer"] for k in sel)/len(held)
    wb=sum(r[v][k]["wer"]<base[k]["wer"]-5e-4 for k in sel); ww=sum(r[v][k]["wer"]>base[k]["wer"]+5e-4 for k in sel)
    print(f"{v:12s} {name}{side}{t}: dev mean Δwer {d:+.4f} ({n} pages fire) | held-out Δwer {dh:+.4f} ({len(sel)} fire, {wb} better, {ww} worse)")
