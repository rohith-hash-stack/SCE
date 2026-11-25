"""Whitespace-only edit mismatches under the current edit message (the one that shows the file's lines), per task:
how many runs hit one, how many persisted (>=3 failed attempts at the same place, refusals of the same call
included), and whether the planned single-line fix would have applied (`old` is one line)."""
import json,glob,os,re,collections,sys
T=collections.defaultdict(lambda: dict(runs=set(),hit=set(),persist=set(),single=0,multi=0,places=collections.Counter(),arms=collections.Counter(),stages=set()))
for p in glob.glob("reports/r4/**/*.jsonl",recursive=True):
    b=os.path.basename(p)
    if b.count("__")!=2: continue
    t,a,s=b[:-6].split("__"); stage=p.split("/")[2]; rid=f"{stage}/{b[:-6]}"
    try: L=[json.loads(l) for l in open(p)]
    except Exception: continue
    T[t]["runs"].add(rid)
    ws={}; fails=collections.Counter()
    for r in L:
        if r.get("tool")!="edit_file": continue
        args=r.get("args") or {}; key=json.dumps(args,sort_keys=True); o=r.get("output_head") or ""
        if "apart from whitespace" in o and "with its spaces" in o:
            m=re.search(r"at line (\d+)",o); pl=(args.get("path","").lstrip("./"),int(m.group(1)))
            ws[key]=pl; fails[pl]+=1
            if len(args.get("old","").strip("\n").split("\n"))==1: T[t]["single"]+=1
            else: T[t]["multi"]+=1
        elif o.startswith("not answered again") and key in ws: fails[ws[key]]+=1
    if fails:
        T[t]["hit"].add(rid); T[t]["stages"].add(stage)
        for pl,n in fails.items():
            if n>=3: T[t]["persist"].add(rid); T[t]["places"][f"{pl[0]}:{pl[1]}"]+=1; T[t]["arms"][a]+=1
out=[]
for t,v in T.items():
    if not v["hit"]: continue
    out.append(dict(task=t,runs=len(v["runs"]),hit=len(v["hit"]),persist=len(v["persist"]),single=v["single"],multi=v["multi"],
                    places=dict(v["places"]),arms=dict(v["arms"]),stages=sorted(v["stages"])))
out.sort(key=lambda r:(-r["persist"]/r["runs"],-r["persist"]))
json.dump(out,open(sys.argv[1],"w"),indent=1)
print(f"{'task':24} runs  hit persist  single/multi-line attempts   persistent places")
for r in out[:25]:
    print(f"{r['task']:24} {r['runs']:>4} {r['hit']:>4} {r['persist']:>4}     {r['single']:>4}/{r['multi']:<4}   {r['places']}")
