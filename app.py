from flask import Flask, request, jsonify, send_from_directory
import os
import yt_dlp
import uuid
import re
import glob
from datetime import datetime

app = Flask(__name__)
DOWNLOAD_FOLDER = os.path.join("static", "downloads")
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

def sanitize_filename(name):
    return re.sub(r'[\\/*?:"<>|]', "", name)

def clean_up_old_files(limit=2):
    files = glob.glob(os.path.join(DOWNLOAD_FOLDER, "*"))
    files = [f for f in files if os.path.isfile(f)]
    if len(files) >= limit:
        # Sort by creation time (oldest first)
        files.sort(key=lambda x: os.path.getctime(x))
        # Delete oldest files, keep only (limit-1)
        for f in files[:len(files) - (limit - 1)]:
            os.remove(f)

@app.route("/")
def home():
    return "🎬 Lucid Video API by Ram Sharma is running!"

@app.route("/api/download", methods=["POST"])
def download_video():
    data = request.get_json()
    url = data.get("url")

    if not url:
        return jsonify({"error": "No URL provided."}), 400

    try:
        # Clean up old files before downloading new one
        clean_up_old_files(limit=2)

        unique_id = str(uuid.uuid4())[:8]
        output_template = os.path.join(DOWNLOAD_FOLDER, f"{unique_id}-%(title)s.%(ext)s")

        ydl_opts = {
            'outtmpl': output_template,
            'format': 'bestvideo+bestaudio/best',
            'merge_output_format': 'mp4',
            'quiet': True,
            'cookiefile': 'cookies.txt'  # You must upload this file manually
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            filename = sanitize_filename(os.path.basename(filename).replace(".webm", ".mp4"))

        return jsonify({
            "status": "success",
            "download_url": f"{request.host_url}static/downloads/{filename}"
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/static/downloads/<filename>")
def serve_file(filename):
    return send_from_directory(DOWNLOAD_FOLDER, filename)


