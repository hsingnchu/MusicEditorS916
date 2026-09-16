import os
import sys
import threading
import webbrowser
import shutil
from pathlib import Path

# Determine base directories
if getattr(sys, 'frozen', False):
    # Running as PyInstaller bundle
    BUNDLE_DIR = sys._MEIPASS
    EXE_DIR = Path(sys.executable).parent
else:
    BUNDLE_DIR = os.path.dirname(os.path.abspath(__file__))
    EXE_DIR = Path(BUNDLE_DIR)

os.chdir(BUNDLE_DIR)

# Add bundled ffmpeg to PATH (packaged in ffmpeg/ subfolder next to exe, or inside bundle)
ffmpeg_locations = [
    Path(BUNDLE_DIR) / "ffmpeg",
    EXE_DIR / "ffmpeg",
    EXE_DIR / "_internal" / "ffmpeg",
]
for loc in ffmpeg_locations:
    if (loc / "ffmpeg.exe").exists():
        os.environ["PATH"] = str(loc) + os.pathsep + os.environ.get("PATH", "")
        break

# Import app after setting up paths
from app import app
import app as app_module

# Set output dir to writable location next to exe
real_output = EXE_DIR / "output"
real_output.mkdir(exist_ok=True)
app_module.OUTPUT_DIR = real_output

PORT = 5001

def open_browser():
    webbrowser.open(f"http://localhost:{PORT}")

if __name__ == "__main__":
    print("=" * 50)
    print("  音樂編輯器")
    print(f"  瀏覽器開啟: http://localhost:{PORT}")
    print("  關閉此視窗即可停止服務")
    print("=" * 50)
    threading.Timer(1.5, open_browser).start()
    app.run(host="0.0.0.0", port=PORT, debug=False)
