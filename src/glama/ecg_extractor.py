#!/usr/bin/env python3
"""ecg_extractor.py: Standalone 0-token parser for Samsung Health Monitor ECG PDFs.
Extracts 500 Hz Lead I time-series, detects R-peaks, and computes HRV metrics.
Usage:
  python ecg_extractor.py <path_to_pdf> [--summary | --csv <out_csv> | --plot <out_png>]
"""
import argparse, json, math, os, re, sys
from pathlib import Path

def parse_pdf_ecg(pdf_path: str) -> dict:
    import pypdf
    reader = pypdf.PdfReader(pdf_path)
    page = reader.pages[0]
    data = page.get_contents().get_data().decode('latin1', errors='replace')
    lines = [l.strip() for l in data.split('\n') if l.strip()]

    # Extract header metadata
    text_all = page.extract_text() or ""
    hr_match = re.search(r"Average heart rate:\s*(\d+)\s*bpm", text_all, re.I)
    date_match = re.search(r"Recorded on\s*([^\n]+)", text_all, re.I)
    rhythm_match = re.search(r"(Sinus rhythm|Atrial fibrillation|Inconclusive)", text_all, re.I)

    # Extract 3 strip blocks (color 1 .4706 .2706 RG)
    blocks = []
    in_ecg = False
    cur = []
    for line in lines:
        if line == '1 .4706 .2706 RG 1 .4706 .2706 rg':
            in_ecg = True
            cur = []
            continue
        if in_ecg:
            if line.endswith(' m'):
                parts = line.split()
                cur.append((float(parts[0]), float(parts[1])))
            elif line.endswith(' c'):
                parts = line.split()
                cur.append((float(parts[4]), float(parts[5])))
            elif line == 'S':
                in_ecg = False
                if len(cur) > 100:
                    blocks.append(cur)

    if not blocks:
        raise ValueError("No ECG waveform curve paths found in PDF.")

    all_y = [pt[1] for b in blocks for pt in b]
    baseline_y = sorted(all_y)[len(all_y)//2]

    # Map to continuous time (s) and voltage (mV)
    time_pts, mv_pts = [], []
    for row_idx, b in enumerate(blocks):
        t_start = row_idx * 10.0
        for pt in b:
            t = t_start + (pt[0] / 992.126) * 10.0
            mv = (baseline_y - pt[1]) / 28.3464567
            time_pts.append(round(t, 4))
            mv_pts.append(round(mv, 4))

    # Basic peak detection
    threshold = 0.25
    min_dist_pts = int(500 * 0.45) # max ~133 bpm
    peaks = []
    last_p = -min_dist_pts
    for i in range(1, len(mv_pts) - 1):
        if mv_pts[i] > threshold and mv_pts[i] > mv_pts[i-1] and mv_pts[i] > mv_pts[i+1]:
            if (i - last_p) >= min_dist_pts:
                peaks.append(i)
                last_p = i

    r_times = [time_pts[p] for p in peaks]
    rr_ms = [(r_times[i] - r_times[i-1]) * 1000.0 for i in range(1, len(r_times))]
    
    mean_hr = 60000.0 / (sum(rr_ms) / len(rr_ms)) if rr_ms else 0.0
    sdnn = math.sqrt(sum((x - (sum(rr_ms)/len(rr_ms)))**2 for x in rr_ms) / len(rr_ms)) if rr_ms else 0.0
    diff_rr = [rr_ms[i] - rr_ms[i-1] for i in range(1, len(rr_ms))]
    rmssd = math.sqrt(sum(d**2 for d in diff_rr) / len(diff_rr)) if diff_rr else 0.0

    return {
        "metadata": {
            "rhythm_classification": rhythm_match.group(1) if rhythm_match else "Unknown",
            "recorded_date": date_match.group(1).strip() if date_match else "Unknown",
            "watch_avg_hr_bpm": int(hr_match.group(1)) if hr_match else None,
            "sampling_rate_hz": 500,
            "duration_s": 30.0,
            "lead": "Lead I"
        },
        "metrics": {
            "computed_mean_hr_bpm": round(mean_hr, 1),
            "beat_count": len(peaks),
            "mean_rr_ms": round(sum(rr_ms)/len(rr_ms), 1) if rr_ms else 0,
            "hrv_sdnn_ms": round(sdnn, 1),
            "hrv_rmssd_ms": round(rmssd, 1),
            "estimated_pvc_count": sum(1 for r in rr_ms if r < 0.6 * (sum(rr_ms)/len(rr_ms)))
        },
        "time_pts": time_pts,
        "mv_pts": mv_pts
    }

def main():
    p = argparse.ArgumentParser()
    p.add_argument("pdf_path")
    p.add_argument("--summary", action="store_true")
    p.add_argument("--csv", help="Output path for CSV")
    p.add_argument("--plot", help="Output path for PNG plot")
    args = p.parse_args()

    res = parse_pdf_ecg(args.pdf_path)
    summary = {
        "metadata": res["metadata"],
        "metrics": res["metrics"],
        "samples_extracted": len(res["time_pts"])
    }
    
    if args.csv:
        import pandas as pd
        df = pd.DataFrame({"time_s": res["time_pts"], "voltage_mv": res["mv_pts"]})
        df.to_csv(args.csv, index=False)
        summary["csv_saved"] = args.csv

    if args.plot:
        import matplotlib.pyplot as plt
        plt.figure(figsize=(12, 4))
        plt.plot(res["time_pts"], res["mv_pts"], color='#E65100', linewidth=0.8)
        plt.title(f"ECG: {res['metadata']['rhythm_classification']} | HR: {res['metrics']['computed_mean_hr_bpm']} bpm | RMSSD: {res['metrics']['hrv_rmssd_ms']} ms")
        plt.xlabel("Time (s)")
        plt.ylabel("mV")
        plt.grid(True, linestyle="--", alpha=0.5)
        plt.tight_layout()
        plt.savefig(args.plot, dpi=150)
        summary["plot_saved"] = args.plot

    print(json.dumps(summary, indent=2))

if __name__ == "__main__":
    main()
