#!/usr/bin/env python3
"""Hagelmonitor voor jeroendunk.nl - draait via GitHub Actions (elke 5 min)."""
import json, os, smtplib, ssl, sys, tempfile
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
import requests, numpy as np, h5py

API = "https://api.dataplatform.knmi.nl/open-data/v1"
KEY = os.environ.get("KNMI_API_KEY", "")
HDR = {"Authorization": KEY}

GRID_LAT_MIN, GRID_LAT_MAX = 50.7, 53.6
GRID_LON_MIN, GRID_LON_MAX = 3.3, 7.3
GRID_ROWS, GRID_COLS = 24, 20
FULL_LAT = (48.9, 55.97); FULL_LON = (0.0, 10.86)

ALERT_MIN_RISK = int(os.environ.get("ALERT_MIN_RISK", "3"))
ALERT_COOLDOWN_MIN = 30

def latest_filename(ds, v):
    r = requests.get(f"{API}/datasets/{ds}/versions/{v}/files", headers=HDR,
                     params={"maxKeys": 1, "orderBy": "created", "sorting": "desc"}, timeout=30)
    r.raise_for_status()
    fs = r.json().get("files", [])
    return fs[0]["filename"] if fs else None

def dl_url(ds, v, fn):
    r = requests.get(f"{API}/datasets/{ds}/versions/{v}/files/{fn}/url", headers=HDR, timeout=30)
    r.raise_for_status(); return r.json()["temporaryDownloadUrl"]

def fetch(url):
    r = requests.get(url, timeout=90); r.raise_for_status(); return r.content

def get_cellwarn():
    try:
        fn = latest_filename("cell-tracking", "2.0")
        if not fn: return {"filename": None, "cells": []}
        geo = json.loads(fetch(dl_url("cell-tracking", "2.0", fn)))
        cells = []
        for f in geo.get("features", []):
            p = f.get("properties", {}) or {}
            cells.append({
                "id": p.get("id") or p.get("cell_id"),
                "hail_prob": p.get("prob_hail", p.get("hail_probability", p.get("probability"))),
                "hail_severity": p.get("sev_hail", p.get("hail_severity", p.get("severity"))),
                "props": p, "geometry": f.get("geometry"),
            })
        return {"filename": fn, "cells": cells}
    except Exception as e:
        print("CellWarn fout:", e); return {"filename": None, "cells": []}

def _find_2d(node):
    for k in node:
        it = node[k]
        if isinstance(it, h5py.Dataset):
            if it.ndim == 2:
                return it[:]
        elif isinstance(it, h5py.Group):
            r = _find_2d(it)
            if r is not None:
                return r
    return None

def read_hail_hdf5(content):
    with tempfile.NamedTemporaryFile(suffix=".h5", delete=False) as t:
        t.write(content); path = t.name
    try:
        with h5py.File(path, "r") as h5:
            arr = _find_2d(h5)
            return np.array(arr, dtype=float) if arr is not None else None
    finally:
        os.unlink(path)

def to_grid(values):
    if values is None: return None
    v = np.where((values < 0) | (values > 200), 0, values)
    H, W = v.shape
    frac = lambda a, lo, hi: (a - lo) / (hi - lo)
    y0 = int((1 - frac(GRID_LAT_MAX, *FULL_LAT)) * H)
    y1 = int((1 - frac(GRID_LAT_MIN, *FULL_LAT)) * H)
    x0 = int(frac(GRID_LON_MIN, *FULL_LON) * W)
    x1 = int(frac(GRID_LON_MAX, *FULL_LON) * W)
    win = v[min(y0,y1):max(y0,y1), min(x0,x1):max(x0,x1)]
    if win.size == 0: return [[0]*GRID_COLS for _ in range(GRID_ROWS)]
    ys = np.linspace(0, win.shape[0], GRID_ROWS+1).astype(int)
    xs = np.linspace(0, win.shape[1], GRID_COLS+1).astype(int)
    out = []
    for i in range(GRID_ROWS):
        row = []
        for j in range(GRID_COLS):
            block = win[ys[i]:ys[i+1], xs[j]:xs[j+1]]
            row.append(float(np.nanmax(block)) if block.size else 0.0)
        out.append(row)
    return out

def get_hail_grid(hist_fn=None):
    ds, v = "radar_hail_warning_5min", "1.0"
    try:
        fn = hist_fn or latest_filename(ds, v)
        if not fn: return {"filename": None, "grid": None}
        return {"filename": fn, "grid": to_grid(read_hail_hdf5(fetch(dl_url(ds, v, fn))))}
    except Exception as e:
        print("Hagelgrid fout:", e); return {"filename": None, "grid": None}

def get_warnings():
    ds, v = "waarschuwingen_nederland_48h", "1.0"
    try:
        fn = latest_filename(ds, v)
        if not fn: return {"filename": None, "text": ""}
        return {"filename": fn, "text": fetch(dl_url(ds, v, fn)).decode("utf-8", "ignore")}
    except Exception as e:
        print("Waarschuwing fout:", e); return {"filename": None, "text": ""}

def risk_and_size(grid):
    if not grid: return 0, "geen"
    peak = max((c for row in grid for c in row), default=0)
    if peak >= 80:   return 5, "zeer groot (>4 cm, golfbal/handpalm)"
    if peak >= 60:   return 4, "groot (2-4 cm)"
    if peak >= 40:   return 3, "matig (1-2 cm)"
    if peak >= 20:   return 2, "klein (<1 cm)"
    if peak > 0:     return 1, "zeer klein / korrel"
    return 0, "geen"

def maybe_alert(level, size, warn):
    if level < ALERT_MIN_RISK: return False
    host = os.environ.get("SMTP_HOST")
    if not host: print("Geen SMTP geconfigureerd, alert overgeslagen"); return False
    sf = ".alert_state"; now = datetime.now(timezone.utc)
    if os.path.exists(sf):
        try:
            if now - datetime.fromisoformat(open(sf).read().strip()) < timedelta(minutes=ALERT_COOLDOWN_MIN):
                return False
        except Exception: pass
    body = (f"HAGELWAARSCHUWING (niveau {level}/5)\nTijd: {now.isoformat()}\n"
            f"Geschatte steengrootte: {size}\n\nGebaseerd op KNMI-radardata via een geautomatiseerde tool.\n"
            f"Raadpleeg ALTIJD knmi.nl voor de officiele waarschuwing.\n\n"
            f"--- KNMI-waarschuwingstekst ---\n{warn[:1500]}")
    msg = MIMEText(body)
    msg["Subject"] = f"[Hagel niveau {level}] jeroendunk.nl"
    msg["From"] = os.environ["ALERT_FROM"]; msg["To"] = os.environ["ALERT_TO"]
    with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587"))) as s:
        s.starttls(context=ssl.create_default_context())
        s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
        s.sendmail(msg["From"], [msg["To"]], msg.as_string())
    open(sf, "w").write(now.isoformat())
    print("Alert verstuurd"); return True

def build(hist_fn=None):
    cw = get_cellwarn()
    hg = get_hail_grid(hist_fn)
    wn = get_warnings() if not hist_fn else {"text": "", "filename": None}
    level, size = risk_and_size(hg["grid"])
    alerted = maybe_alert(level, size, wn["text"]) if not hist_fn else False
    return {
        "generated": datetime.now(timezone.utc).isoformat(),
        "mode": "historisch" if hist_fn else "realtime",
        "risk_level": level, "estimated_size": size,
        "grid": hg["grid"], "grid_rows": GRID_ROWS, "grid_cols": GRID_COLS,
        "bbox": [GRID_LAT_MIN, GRID_LON_MIN, GRID_LAT_MAX, GRID_LON_MAX],
        "cells": cw["cells"], "cell_count": len(cw["cells"]),
        "warnings": wn["text"][:4000],
        "sources": {"cellwarn": cw["filename"], "hail": hg["filename"], "warnings": wn["filename"]},
        "alert_sent": alerted,
        "disclaimer": "Indicatief. KNMI (knmi.nl) is de officiele bron voor weerwaarschuwingen.",
    }

if __name__ == "__main__":
    out = build()
    os.makedirs("out", exist_ok=True)
    with open("out/hagel_status.json", "w") as f: json.dump(out, f)
    print(f"OK level={out['risk_level']} cells={out['cell_count']} alert={out['alert_sent']}")
