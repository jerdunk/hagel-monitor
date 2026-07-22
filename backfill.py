#!/usr/bin/env python3
"""Verwerk een historische periode uit het KNMI-tar-archief (radar_tar_hail_warning_5min).
Gebruik:  python backfill.py 2026-06-27 2026-06-28
Genereert out/hist_<YYYYMMDDHHMM>.json per 5-minuten-moment, voor verificatie op de website."""
import json, os, sys, tarfile, io
from datetime import datetime, timezone
import requests, numpy as np
import hagel_monitor as hm  # hergebruik grid-logica

def day_tar(datestr):
    d = datetime.strptime(datestr, "%Y-%m-%d")
    nxt = d.replace(hour=0)
    end = (d.toordinal()+1)
    fn = f"RAD25_OPER_R___TARHAW__L2__{d.strftime('%Y%m%d')}T000000_{datetime.fromordinal(end).strftime('%Y%m%d')}T000000_0001.tar"
    return fn

def process_day(datestr):
    ds, v = "radar_tar_hail_warning_5min", "1.0"
    fn = day_tar(datestr)
    print("Download", fn)
    content = hm.fetch(hm.dl_url(ds, v, fn))
    os.makedirs("out", exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(content)) as tar:
        for m in tar.getmembers():
            if not m.name.endswith(".h5"): continue
            h5 = tar.extractfile(m).read()
            grid = hm.to_grid(hm.read_hail_hdf5(h5))
            level, size = hm.risk_and_size(grid)
            # timestamp uit bestandsnaam (…_YYYYMMDDHHMM.h5)
            ts = "".join([c for c in m.name if c.isdigit()])[-12:]
            out = {"generated": ts, "mode": "historisch", "risk_level": level,
                   "estimated_size": size, "grid": grid, "grid_rows": hm.GRID_ROWS,
                   "grid_cols": hm.GRID_COLS, "bbox":[hm.GRID_LAT_MIN,hm.GRID_LON_MIN,hm.GRID_LAT_MAX,hm.GRID_LON_MAX],
                   "cells": [], "cell_count": 0, "warnings": "",
                   "disclaimer":"Historische reconstructie uit KNMI-archief."}
            with open(f"out/hist_{ts}.json", "w") as f: json.dump(out, f)
    print("Klaar:", datestr)

if __name__ == "__main__":
    for ds in sys.argv[1:] or ["2026-06-27"]:
        process_day(ds)
