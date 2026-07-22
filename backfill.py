#!/usr/bin/env python3
"""Verwerk een historische periode uit het KNMI-tar-archief (radar_tar_hail_warning_5min).
Gebruik:  python backfill.py 2026-06-27 2026-06-28
Genereert out/hist_<YYYYMMDDHHMM>.json per 5-minuten-moment, voor verificatie op de website."""
import json, os, sys, tarfile, io, time
from datetime import datetime, timezone
import requests, numpy as np
import hagel_monitor as hm  # hergebruik grid-logica

def fetch_with_retry(url, max_tries=6, base_delay=5):
    """Download met exponential backoff; vangt 429 (Too Many Requests) van de
    gedeelde anonieme KNMI-sleutel op."""
    for attempt in range(1, max_tries + 1):
        try:
            return hm.fetch(url)
        except requests.HTTPError as e:
            code = getattr(e.response, "status_code", None)
            if code == 429 and attempt < max_tries:
                wait = base_delay * (2 ** (attempt - 1))
                print(f"  429 Too Many Requests - poging {attempt}/{max_tries}, wacht {wait}s")
                time.sleep(wait)
                continue
            raise
    raise RuntimeError("Kon bestand niet ophalen na herhaalde 429-fouten")

def day_tar(datestr):
    d = datetime.strptime(datestr, "%Y-%m-%d")
    end = (d.toordinal()+1)
    fn = f"RAD25_OPER_R___TARHAW__L2__{d.strftime('%Y%m%d')}T000000_{datetime.fromordinal(end).strftime('%Y%m%d')}T000000_0001.tar"
    return fn

def process_day(datestr):
    ds, v = "radar_tar_hail_warning_5min", "1.0"
    fn = day_tar(datestr)
    print("Download", fn)
    content = fetch_with_retry(hm.dl_url(ds, v, fn))
    os.makedirs("out", exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(content)) as tar:
        members = [m for m in tar.getmembers() if m.name.endswith(".h5")]
        for i, m in enumerate(members):
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
    for i, ds in enumerate(sys.argv[1:] or ["2026-06-27"]):
        if i > 0:
            time.sleep(3)  # kleine pauze tussen dagen om de gedeelde sleutel te ontzien
        process_day(ds)
