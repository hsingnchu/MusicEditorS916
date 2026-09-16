import os
import sys
import subprocess
import uuid
import shutil
from pathlib import Path

from flask import Flask, request, jsonify, send_from_directory, send_file

app = Flask(__name__, static_folder=".", static_url_path="")

OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

# Auto-detect ffmpeg from common install locations
_FFMPEG_SEARCH_ROOTS = [
    Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages",
    Path("C:/ProgramData/chocolatey/bin"),
    Path("C:/ffmpeg/bin"),
]

FFMPEG_PATH = None  # will be set to full path of ffmpeg.exe if found

def _find_ffmpeg():
    global FFMPEG_PATH
    w = shutil.which("ffmpeg")
    if w:
        FFMPEG_PATH = w
        return
    for root in _FFMPEG_SEARCH_ROOTS:
        if not root.exists():
            continue
        try:
            for f in root.rglob("ffmpeg.exe"):
                FFMPEG_PATH = str(f)
                os.environ["PATH"] = str(f.parent) + os.pathsep + os.environ.get("PATH", "")
                return
        except Exception:
            continue

_find_ffmpeg()


# --------------- dependency check / install ---------------

def check_dep(name):
    """Return True if the dependency is available."""
    if name == "ffmpeg":
        return FFMPEG_PATH is not None
    if name == "yt-dlp":
        if shutil.which("yt-dlp"):
            return True
        # might be installed as python module
        try:
            subprocess.run([sys.executable, "-m", "yt_dlp", "--version"],
                           capture_output=True, timeout=5)
            return True
        except Exception:
            return False
    # pip packages
    try:
        __import__(name)
        return True
    except ImportError:
        return False


@app.route("/api/check-deps")
def api_check_deps():
    deps = {
        "ffmpeg": check_dep("ffmpeg"),
        "yt-dlp": check_dep("yt-dlp"),
        "pydub": check_dep("pydub"),
    }
    return jsonify(deps)


@app.route("/api/install-dep", methods=["POST"])
def api_install_dep():
    name = request.json.get("name")
    try:
        if name == "ffmpeg":
            for cmd in [
                ["winget", "install", "--id", "Gyan.FFmpeg", "-e", "--accept-source-agreements", "--accept-package-agreements"],
                ["choco", "install", "ffmpeg", "-y"],
            ]:
                try:
                    subprocess.check_call(cmd)
                    break
                except (FileNotFoundError, subprocess.CalledProcessError):
                    continue
            _find_ffmpeg()
            return jsonify({"ok": True})
        elif name == "yt-dlp":
            subprocess.check_call([sys.executable, "-m", "pip", "install", "yt-dlp"])
            return jsonify({"ok": True})
        elif name == "pydub":
            subprocess.check_call([sys.executable, "-m", "pip", "install", "pydub", "audioop-lts"])
            return jsonify({"ok": True})
        else:
            return jsonify({"ok": False, "error": "Unknown dependency"}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# --------------- Section 1: extract audio from URL ---------------

@app.route("/api/extract", methods=["POST"])
def api_extract():
    url = request.json.get("url", "").strip()
    if not url:
        return jsonify({"ok": False, "error": "URL is required"}), 400

    out_id = uuid.uuid4().hex[:8]
    out_path = str(OUTPUT_DIR / f"extract_{out_id}.mp3")

    try:
        yt_dlp_cmd = ["yt-dlp"] if shutil.which("yt-dlp") else [sys.executable, "-m", "yt_dlp"]
        cmd = yt_dlp_cmd + [
            "-x",
            "--audio-format", "mp3",
            "--audio-quality", "0",
            "-o", out_path.replace(".mp3", ".%(ext)s"),
            url,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            return jsonify({"ok": False, "error": result.stderr[:500]}), 500

        # yt-dlp may output with different extension then convert
        # find the actual file
        actual = None
        for f in OUTPUT_DIR.glob(f"extract_{out_id}.*"):
            actual = f
            break
        if actual is None:
            return jsonify({"ok": False, "error": "No output file generated"}), 500

        return jsonify({"ok": True, "file": actual.name})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# --------------- Section 2: trim audio ---------------

@app.route("/api/trim", methods=["POST"])
def api_trim():
    from pydub import AudioSegment

    file = request.files.get("file")
    start_str = request.form.get("start", "").strip()
    end_str = request.form.get("end", "").strip()

    if not file:
        return jsonify({"ok": False, "error": "No file provided"}), 400

    # save uploaded file
    tmp_id = uuid.uuid4().hex[:8]
    ext = Path(file.filename).suffix or ".mp3"
    tmp_path = OUTPUT_DIR / f"tmp_{tmp_id}{ext}"
    file.save(str(tmp_path))

    try:
        audio = AudioSegment.from_file(str(tmp_path))

        start_ms = _parse_time(start_str) if start_str else 0
        end_ms = _parse_time(end_str) if end_str else len(audio)

        trimmed = audio[start_ms:end_ms]

        out_name = f"trim_{tmp_id}.mp3"
        out_path = OUTPUT_DIR / out_name
        trimmed.export(str(out_path), format="mp3")

        return jsonify({"ok": True, "file": out_name})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        tmp_path.unlink(missing_ok=True)


# --------------- Section 3: concatenate audio ---------------

@app.route("/api/concat", methods=["POST"])
def api_concat():
    from pydub import AudioSegment

    files = request.files.getlist("files")
    if len(files) < 2:
        return jsonify({"ok": False, "error": "Need at least 2 files"}), 400

    tmp_paths = []
    try:
        combined = AudioSegment.empty()
        for f in files:
            tmp_id = uuid.uuid4().hex[:8]
            ext = Path(f.filename).suffix or ".mp3"
            tmp_path = OUTPUT_DIR / f"tmp_{tmp_id}{ext}"
            f.save(str(tmp_path))
            tmp_paths.append(tmp_path)
            combined += AudioSegment.from_file(str(tmp_path))

        out_name = f"concat_{uuid.uuid4().hex[:8]}.mp3"
        out_path = OUTPUT_DIR / out_name
        combined.export(str(out_path), format="mp3")

        return jsonify({"ok": True, "file": out_name})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        for p in tmp_paths:
            Path(p).unlink(missing_ok=True)


# --------------- download ---------------

@app.route("/api/download/<filename>")
def api_download(filename):
    return send_from_directory(str(OUTPUT_DIR), filename, as_attachment=True)


# --------------- serve index.html ---------------

@app.route("/")
def index():
    return send_file("index.html")


# --------------- helpers ---------------

def _parse_time(s):
    """Parse time string like '1:30', '01:02:03', '90' into milliseconds."""
    s = s.strip()
    parts = s.split(":")
    parts = [float(p) for p in parts]
    if len(parts) == 1:
        return int(parts[0] * 1000)
    elif len(parts) == 2:
        return int((parts[0] * 60 + parts[1]) * 1000)
    elif len(parts) == 3:
        return int((parts[0] * 3600 + parts[1] * 60 + parts[2]) * 1000)
    raise ValueError(f"Invalid time format: {s}")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    print(f"Music Editor running at http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
