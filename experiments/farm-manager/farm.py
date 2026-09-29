#!/usr/bin/env python3
"""Small deterministic farm mock: placement recommendation, never hardware control."""
import json
from pathlib import Path

PRINTERS = [
    {"id":"Ender-1","status":"printing","free_at_h":2.0,"volume_mm":[220,220,250],"materials":["PLA"]},
    {"id":"Ender-2","status":"idle","free_at_h":0,"volume_mm":[220,220,250],"materials":["PLA","PETG"]},
    {"id":"MK4-1","status":"offline","free_at_h":None,"volume_mm":[250,210,220],"materials":["PLA","PETG"]},
]


def recommend(job, printers=PRINTERS):
    dims, mat, duration = job["dimensions_mm"], job["material"], job["hours"]
    choices=[]
    for p in printers:
        if p["status"] == "offline" or mat not in p["materials"]:
            continue
        if any(dims[i] > p["volume_mm"][i] for i in range(3)):
            continue
        choices.append((p["free_at_h"] + duration, p))
    if not choices:
        return {"job_id":job["id"],"status":"waiting","reason":"no online compatible printer"}
    finish, printer = min(choices, key=lambda x:x[0])
    return {"job_id":job["id"],"status":"recommended","printer":printer["id"],
            "queued_start_h":printer["free_at_h"],"finish_in_h":round(finish,2),
            "reason":f"compatible {mat}; estimated earliest finish"}


def main():
    example = {"id":"JOB-001","dimensions_mm":[80,80,22],"material":"PLA","hours":3.7}
    print(json.dumps({"mode":"mock; no printer connection","printers":PRINTERS,
                      "available_now":[p["id"] for p in PRINTERS if p["status"]=="idle"],
                      "recommendation":recommend(example),
                      "capacity_24h_hours":sum(max(0,24-(p["free_at_h"] or 0)) for p in PRINTERS if p["status"]!="offline")},indent=2))


if __name__ == "__main__": main()
