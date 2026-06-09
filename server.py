"""
NASRDA Pan-Sharpening Engine - Local Flask Server
Run with: python server.py
Then open: http://localhost:5000
"""

import os
import uuid
import threading
import traceback
from pathlib import Path
from flask import Flask, request, jsonify, send_file, send_from_directory, make_response

app = Flask(__name__)

OUTPUT_DIR = Path("./nasrda_pansharp_output")
OUTPUT_DIR.mkdir(exist_ok=True)

jobs = {}

# ── CORS — applied to every single response ───────────────────────────────
def cors(response):
    response.headers["Access-Control-Allow-Origin"]  = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Accept"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS, HEAD"
    return response

app.after_request(cors)

# ── Handle ALL OPTIONS preflight requests ─────────────────────────────────
@app.before_request
def handle_options():
    if request.method == "OPTIONS":
        resp = make_response("", 200)
        resp.headers["Access-Control-Allow-Origin"]  = "*"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Accept"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS, HEAD"
        return resp

# ── Serve dashboard ────────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory(".", "nasrda_dashboard.html")

# ── Serve favicon (stop 405 errors) ───────────────────────────────────────
@app.route("/favicon.ico")
def favicon():
    return "", 204

# ── POST /api/run ──────────────────────────────────────────────────────────
@app.route("/api/run", methods=["POST", "OPTIONS"])
def run_pipeline():
    if request.method == "OPTIONS":
        return "", 200

    if "ms_file" not in request.files or "pan_file" not in request.files:
        return jsonify({"error": "Both ms_file and pan_file are required"}), 400

    ms_file  = request.files["ms_file"]
    pan_file = request.files["pan_file"]
    method   = request.form.get("method",   "brovey")
    resample = request.form.get("resample", "bilinear")

    job_id   = str(uuid.uuid4())[:8]
    work_dir = Path(f"./nasrda_jobs/{job_id}")
    work_dir.mkdir(parents=True, exist_ok=True)

    ms_path  = work_dir / ms_file.filename
    pan_path = work_dir / pan_file.filename
    ms_file.save(ms_path)
    pan_file.save(pan_path)

    jobs[job_id] = {
        "status": "queued",
        "stage":  0,
        "logs":   [],
        "result": None,
        "error":  None,
    }

    thread = threading.Thread(
        target=_run_job,
        args=(job_id, ms_path, pan_path, method, resample),
        daemon=True,
    )
    thread.start()

    return jsonify({"job_id": job_id, "status": "queued"})


# ── GET /api/status/<job_id> ───────────────────────────────────────────────
@app.route("/api/status/<job_id>", methods=["GET", "OPTIONS"])
def get_status(job_id):
    if request.method == "OPTIONS":
        return "", 200
    if job_id not in jobs:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(jobs[job_id])


# ── GET /api/download/<job_id> ─────────────────────────────────────────────
@app.route("/api/download/<job_id>", methods=["GET", "OPTIONS"])
def download(job_id):
    if request.method == "OPTIONS":
        return "", 200
    if job_id not in jobs:
        return jsonify({"error": "Job not found"}), 404
    job = jobs[job_id]
    if job["status"] != "complete" or not job.get("result"):
        return jsonify({"error": "Job not complete yet"}), 400
    out_path = Path(job["result"]["output_file"])
    if not out_path.exists():
        return jsonify({"error": "Output file missing"}), 404
    return send_file(
        str(out_path.resolve()),
        as_attachment=True,
        download_name=out_path.name,
        mimetype="image/tiff"
    )


# ── Background worker ──────────────────────────────────────────────────────
def _run_job(job_id, ms_path, pan_path, method, resample):
    job = jobs[job_id]
    job["status"] = "running"

    def log(level, msg):
        job["logs"].append({"level": level, "msg": msg})
        print(f"[{level}] {msg}")

    try:
        log("INFO", f"Job {job_id} — method: {method.upper()}")
        log("INFO", f"MS  : {ms_path.name}")
        log("INFO", f"PAN : {pan_path.name}")

        try:
            from nasrda_pansharpening_engine import (
                NASRDAPanSharpeningPipeline,
                PanSharpenConfig,
            )
            from rasterio.enums import Resampling

            resample_map = {
                "bilinear": Resampling.bilinear,
                "bicubic":  Resampling.cubic,
                "nearest":  Resampling.nearest,
                "lanczos":  Resampling.lanczos,
            }

            config = PanSharpenConfig(
                method          = method,
                resample_kernel = resample_map.get(resample, Resampling.bilinear),
                output_dir      = OUTPUT_DIR,
            )

            # Patch the engine logger to capture stage updates
            import nasrda_pansharpening_engine as eng
            _orig_log = eng.log

            def _patched_log(stage, msg):
                job["stage"] = stage
                log("INFO", f"Stage {stage}/4: {msg}")
                _orig_log(stage, msg)

            eng.log = _patched_log

            job["stage"] = 1
            log("INFO", "Stage 1/4: Aligning MS to PAN pixel grid...")

            pipeline = NASRDAPanSharpeningPipeline(config)
            result   = pipeline.run(ms_path=ms_path, pan_path=pan_path)

            eng.log = _orig_log

            job["stage"]  = 4
            job["status"] = "complete"
            job["result"] = result

            log("OK", f"Pipeline complete in {result['processing_time_s']}s")
            log("OK", f"Output: {Path(result['output_file']).name}")
            log("OK", f"Dimensions: {result['width']} x {result['height']} px")

        except ImportError as ie:
            log("WARN", f"Engine import error: {ie}")
            log("WARN", "Running simulation — place nasrda_pansharpening_engine.py in same folder")
            _simulate(job_id, log)

    except Exception as e:
        job["status"] = "failed"
        job["error"]  = str(e)
        log("ERR", f"Failed: {e}")
        log("ERR", traceback.format_exc())


def _simulate(job_id, log):
    import time
    job = jobs[job_id]
    steps = [
        (1, "Resampling MS bands to PAN pixel grid..."),
        (2, "Decomposing colour channels..."),
        (3, "Fusing PAN spatial detail into MS colour..."),
        (4, "Writing GeoTIFF output..."),
    ]
    for s, msg in steps:
        job["stage"] = s
        log("INFO", f"Stage {s}/4: {msg}")
        time.sleep(1.0)

    job["status"] = "complete"
    job["result"] = {
        "status":            "simulated",
        "method":            "brovey",
        "output_file":       "simulation_no_output.tif",
        "bands":             3,
        "width":             2135,
        "height":            2033,
        "processing_time_s": 4.0,
    }
    log("OK", "Simulation complete.")
    log("WARN", "This is a preview only — no real GeoTIFF was written.")
    log("WARN", "Check nasrda_pansharpening_engine.py is in the same folder as server.py")


# ── Start ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print()
    print("=" * 54)
    print("  NASRDA Pan-Sharpening Engine")
    print("=" * 54)
    print("  Dashboard : http://localhost:5000")
    print("  API       : http://localhost:5000/api")
    print("  Ctrl+C    : stop server")
    print("=" * 54)
    print()
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
