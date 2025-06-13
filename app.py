from flask import Flask, request, jsonify, send_from_directory
import os
import yt_dlp
import uuid
import re

app = Flask(__name__)
DOWNLOAD_FOLDER = os.path.join("static", "downloads")
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

def sanitize_filename(name):
    return re.sub(r'[\\/*?:"<>|]', "", name)

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
        unique_id = str(uuid.uuid4())[:8]
        output_template = os.path.join(DOWNLOAD_FOLDER, f"{unique_id}-%(title)s.%(ext)s")

        ydl_opts = {
            'outtmpl': output_template,
            'format': 'bestvideo+bestaudio/best',
            'merge_output_format': 'mp4',
            'quiet': True
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
