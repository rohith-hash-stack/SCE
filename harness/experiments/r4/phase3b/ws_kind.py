"""Classify each whitespace-mismatch edit (current message): would a single-line, leading-whitespace-only fix apply?"""
import json,glob,os,re,collections,sys
K=collections.defaultdict(collections.Counter); fam={}
for t in json.load(open("harness/experiments/r4/phase3/poc2.json"))["tasks"]: fam[t["id"]]=t.get("family")
for p in glob.glob("reports/r4/**/*.jsonl",recursive=True):
    b=os.path.basename(p)
    if b.count("__")!=2: continue
    t=b.split("__")[0]
    for l in open(p):
        r=json.loads(l); o=r.get("output_head") or ""
        if r.get("tool")!="edit_file" or "with its spaces" not in o: continue
        old=(r.get("args") or {}).get("old","")
        if len(old.strip("\n").split("\n"))!=1: K[t]["multi-line"]+=1; continue
        m=re.search(r"with its spaces\):\s*\n\d+\t([^\n]*)",o); f=(m.group(1) if m else "")
        a,bb=old.strip(),f.strip()
        if a==bb or (bb and a.startswith(bb) and len(o)>=len(o.rstrip())): K[t]["single-line, leading whitespace"]+=1
        elif a in f: K[t]["single-line, part of the line"]+=1
        else: K[t]["single-line, unclear"]+=1
for t in sorted(K,key=lambda t:-sum(K[t].values())):
    print(f"{t:24} {dict(K[t])}")
