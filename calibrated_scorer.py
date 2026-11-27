#!/usr/bin/env python3
"""Apply a trained isotonic calibration artifact to one JSON launch event."""
import json,sys
from spl_rug_radar import score_launch
def calibrate(p,knots):
    for edge,value in knots:
        if p<=edge:return value
    return knots[-1][1]
def main():
    path=sys.argv[1] if len(sys.argv)>1 else "calibration.json"
    model=json.load(open(path))
    if model.get("method")!="isotonic-pav" or not model.get("knots"):raise ValueError("invalid calibration artifact")
    result=score_launch(json.load(sys.stdin))
    if result["rug_probability"] is not None:
        raw=result["rug_probability"]; result["rug_probability"]=round(calibrate(raw,model["knots"]),4)
        result["momentum_probability"]=round(max(0,min(1,1-result["rug_probability"])),4)
        result["calibration"]="isotonic; trained on "+str(model["train_n"])+" chronological samples"
    print(json.dumps(result,separators=(",",":")))
if __name__=="__main__":main()
