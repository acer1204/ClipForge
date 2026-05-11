import os
import re
import json
import uuid
import subprocess
from flask import Flask, request, jsonify, send_file, send_from_directory, abort


def sanitize_name(name: str) -> str:
    """Convert user-supplied segment name to a safe filename (no extension)."""
    name = re.sub(r'[\\/:*?"<>|]', '_', name)  # remove illegal chars
    name = name.strip('. ')                       # no leading/trailing dots or spaces
    return name[:80] or 'segment'                 # limit length

app = Flask(__name__, static_folder=".")

UPLOAD_DIR = "uploads"
OUTPUT_DIR = "outputs"
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)


@app.route("/")
def index():
    return send_file("index.html")


@app.route("/upload", methods=["POST"])
def upload():
    if "video" not in request.files:
        return jsonify({"error": "未收到影片檔案"}), 400
    f = request.files["video"]
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in (".mp4", ".mov", ".avi", ".mkv", ".webm"):
        return jsonify({"error": "不支援的格式"}), 400
    filename = uuid.uuid4().hex + ext
    path = os.path.join(UPLOAD_DIR, filename)
    f.save(path)

    # 取得影片時長
    probe = subprocess.run(
        [
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            path,
        ],
        capture_output=True, text=True,
    )
    info = json.loads(probe.stdout)
    duration = float(info["format"]["duration"])
    return jsonify({"filename": filename, "duration": duration})


@app.route("/video/<filename>")
def serve_video(filename):
    path = os.path.join(UPLOAD_DIR, filename)
    if not os.path.isfile(path):
        abort(404)
    return send_file(path, conditional=True)


@app.route("/split", methods=["POST"])
def split():
    data = request.get_json()
    filename = data.get("filename")
    splits = sorted(float(t) for t in data.get("splits", []))
    names   = data.get("names", [])
    enabled = data.get("enabled", [])

    input_path = os.path.join(UPLOAD_DIR, filename)
    if not os.path.isfile(input_path):
        return jsonify({"error": "找不到原始影片"}), 404

    # 取得時長
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", input_path],
        capture_output=True, text=True,
    )
    duration = float(json.loads(probe.stdout)["format"]["duration"])

    trim_start = float(data.get("trimStart", 0))
    trim_end   = float(data.get("trimEnd", duration))
    trim_start = max(0.0, trim_start)
    trim_end   = min(duration, trim_end)

    # only keep splits within trim range
    splits = [t for t in splits if trim_start < t < trim_end]
    times = [trim_start] + splits + [trim_end]
    base = uuid.uuid4().hex
    segments = []

    for i in range(len(times) - 1):
        start = times[i]
        end = times[i + 1]

        # disabled segment → placeholder entry so index stays aligned with frontend
        if i < len(enabled) and not enabled[i]:
            segments.append({"filename": None, "start": start, "end": end,
                             "size": 0, "enabled": False})
            continue

        raw_name = names[i] if i < len(names) else f"片段 {i + 1}"
        safe_name = sanitize_name(raw_name)
        out_name = f"{safe_name}.mp4"
        # handle duplicate filenames
        out_path_check = os.path.join(OUTPUT_DIR, out_name)
        if os.path.exists(out_path_check):
            out_name = f"{safe_name}_{base[:6]}.mp4"
        out_path = os.path.join(OUTPUT_DIR, out_name)
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-ss", str(start),
                "-to", str(end),
                "-i", input_path,
                "-c:v", "libx264", "-c:a", "aac",
                "-avoid_negative_ts", "make_zero",
                out_path,
            ],
            capture_output=True,
        )
        size = os.path.getsize(out_path)
        segments.append({"filename": out_name, "start": start, "end": end, "size": size})

    return jsonify({"segments": segments})


@app.route("/output/<filename>")
def serve_output(filename):
    path = os.path.join(OUTPUT_DIR, filename)
    if not os.path.isfile(path):
        abort(404)
    return send_file(path, as_attachment=True)


if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000)
