#!/usr/bin/env python3
"""Chronological, leakage-aware calibration/backtest for SPL launch JSONL data."""
import argparse, json, math, pathlib, sys
from spl_rug_radar import score_launch

def pav(rows):
    """Pool-adjacent-violators isotonic fit. Returns [upper_score, calibrated_rate]."""
    blocks=[]
    for p,y in sorted(rows):
        blocks.append([p,p,float(y),1])
        while len(blocks)>1 and blocks[-2][2]/blocks[-2][3] > blocks[-1][2]/blocks[-1][3]:
            b=blocks.pop(); a=blocks.pop()
            blocks.append([a[0],b[1],a[2]+b[2],a[3]+b[3]])
    return [[round(b[1],8),round(b[2]/b[3],8)] for b in blocks]

def apply(p, knots):
    for edge,value in knots:
        if p<=edge:return value
    return knots[-1][1]

def chronological_split(data,fraction=.7):
    """Split only between launch slots so same-slot launches cannot straddle sets."""
    counts={}
    for row in data:counts[row[0]]=counts.get(row[0],0)+1
    slots=sorted(counts)
    if len(slots)<2:raise ValueError("need at least two distinct launch slots for a chronological split")
    target=max(1,min(len(data)-1,int(len(data)*fraction)))
    seen=0; cut_slot=None
    for slot in slots[:-1]:
        seen+=counts[slot]
        if seen>=target:
            cut_slot=slot
            break
    if cut_slot is None:cut_slot=slots[-2]
    train=[row for row in data if row[0]<=cut_slot]
    test=[row for row in data if row[0]>cut_slot]
    return train,test


def ranking_metrics(rows):
    """Tie-aware ROC AUC and average precision; None when one class is absent."""
    positives=sum(bool(y) for _,y in rows); negatives=len(rows)-positives
    if not positives or not negatives:return {"roc_auc":None,"average_precision":None}
    ordered=sorted(rows,key=lambda x:x[0])
    rank_sum=0.0
    for i,(score,_) in enumerate(ordered):
        left=i
        while left and ordered[left-1][0]==score:left-=1
        right=i+1
        while right<len(ordered) and ordered[right][0]==score:right+=1
        avg_rank=((left+1)+right)/2
        if ordered[i][1]:rank_sum+=avg_rank
    auc=(rank_sum-positives*(positives+1)/2)/(positives*negatives)
    descending=sorted(rows,key=lambda x:x[0],reverse=True)
    tp=fp=0; ap=0.0; i=0
    while i<len(descending):
        j=i
        while j<len(descending) and descending[j][0]==descending[i][0]:
            if descending[j][1]:tp+=1
            else:fp+=1
            j+=1
        group_pos=sum(bool(y) for _,y in descending[i:j])
        if group_pos:ap+=(group_pos/positives)*(tp/(tp+fp))
        i=j
    return {"roc_auc":round(auc,5),"average_precision":round(ap,5)}


def metrics(rows):
    tp=sum(p>=.5 and y for p,y in rows); fp=sum(p>=.5 and not y for p,y in rows)
    tn=sum(p<.5 and not y for p,y in rows); fn=sum(p<.5 and y for p,y in rows)
    n=len(rows); ece=0.0
    for i in range(10):
        g=[(p,y) for p,y in rows if i/10<=p< (i+1)/10 or (i==9 and p==1)]
        if g: ece+=len(g)/n*abs(sum(p for p,y in g)/len(g)-sum(y for p,y in g)/len(g))
    return {"n":n,"tp":tp,"fp":fp,"tn":tn,"fn":fn,
      "precision":round(tp/max(1,tp+fp),4),"recall":round(tp/max(1,tp+fn),4),
      "f1":round(2*tp/max(1,2*tp+fp+fn),4),
      "brier":round(sum((p-y)**2 for p,y in rows)/n,5),"ece":round(ece,5),
      **ranking_metrics(rows)}

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("jsonl"); ap.add_argument("--calibration",default="calibration.json")
    ap.add_argument("--min-samples",type=int,default=50); args=ap.parse_args()
    data=[]
    for line_no,line in enumerate(pathlib.Path(args.jsonl).read_text().splitlines(),1):
        if not line.strip():continue
        row=json.loads(line); label=row.pop("label")
        if label not in (0,1,False,True):raise ValueError(f"line {line_no}: label must be 0 or 1")
        pred=score_launch(row)
        if pred["in_window"] and pred["rug_probability"] is not None:
            data.append((int(row["launch_slot"]),float(pred["rug_probability"]),int(label)))
    data.sort()
    if len(data)<args.min_samples:raise SystemExit(f"need at least {args.min_samples} labeled in-window launches; got {len(data)}")
    train,test=chronological_split(data)
    if len({x[2] for x in train})<2 or len({x[2] for x in test})<2:
        raise SystemExit("chronological train and holdout partitions must each contain both labels")
    knots=pav([(p,y) for _,p,y in train])
    raw=[(p,y) for _,p,y in test]; calibrated=[(apply(p,knots),y) for _,p,y in test]
    artifact={"method":"isotonic-pav","fit":"chronological first ~70%; holdout last ~30%, split only between launch slots",
      "train_n":len(train),"holdout_n":len(test),"knots":knots,
      "warning":"Calibration is valid only for the documented data source, label definition and time period."}
    pathlib.Path(args.calibration).write_text(json.dumps(artifact,indent=2)+"\n")
    print(json.dumps({"dataset_n":len(data),"train":metrics([(p,y) for _,p,y in train]),
      "holdout_raw":metrics(raw),"holdout_calibrated":metrics(calibrated),
      "calibration":args.calibration},indent=2))
if __name__=="__main__":main()
