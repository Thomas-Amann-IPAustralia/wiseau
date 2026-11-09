"""Table Transformer's raw table detections on every scored scan (roadmap 14 item 1.3).

For each page: every 'table' box TATR's detection model scores >= 0.5, as (score,
[cx, cy, w, h] as fractions of the page). Used to test whether confidence or box
size separates real tables from two-column prose (it doesn't; see
docs/benchmarks/2026-11-09-recogniser-and-table-bakeoff.md). Renders at 100 dpi and
resizes to 800 px on the long side, so scores can differ slightly from the tatr
engine's (300 dpi, same resize). Run with the TATR venv's python:

  python benchmarks/bakeoff/tatr_detections.py benchmarks/bakeoff/survey/tatr-detections.json
"""
import json, sys, os
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent.parent; sys.path.insert(0,str(ROOT/"benchmarks/bakeoff"))
import numpy as np, pymupdf, torch
from PIL import Image
from transformers import TableTransformerForObjectDetection
from regions import *
torch.set_num_threads(2)
m=TableTransformerForObjectDetection.from_pretrained("microsoft/table-transformer-detection").eval()
mean=torch.tensor([0.485,0.456,0.406]).view(3,1,1); std=torch.tensor([0.229,0.224,0.225]).view(3,1,1)
out={}
for s in (SAMPLE,SAMPLE_B,SAMPLE_C,SAMPLE_D,SAMPLE_E,SAMPLE_F,SAMPLE_L):
    for k,ns in s.items():
        if k=="V16": continue
        d=pymupdf.open(ROOT/DOCS[k])
        for n in ns:
            pix=d[n-1].get_pixmap(dpi=100,alpha=False); im=Image.frombytes("RGB",(pix.width,pix.height),pix.samples)
            sc=800/max(im.size); im=im.resize((round(im.width*sc),round(im.height*sc)),Image.BILINEAR)
            x=((torch.from_numpy(np.asarray(im,np.float32)/255).permute(2,0,1)-mean)/std).unsqueeze(0)
            with torch.inference_mode(): o=m(pixel_values=x)
            p=o.logits.softmax(-1)[0,:,:-1]; scores,labels=p.max(-1)
            dets=[(round(s_,3),[round(v,3) for v in b]) for s_,l,b in zip(scores.tolist(),labels.tolist(),o.pred_boxes[0].tolist()) if s_>=0.5 and m.config.id2label[l]=="table"]
            out[f"{k}_p{n}"]=dets
json.dump(out,open(sys.argv[1],"w"),indent=1); print(len(out))
