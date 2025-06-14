from flask import Flask, request, jsonify, send_from_directory
import os
import yt_dlp
import uuid
import re
import glob
import urllib.parse
from datetime import datetime

app = Flask(__name__)
DOWNLOAD_FOLDER = os.path.join("static", "downloads")
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

def sanitize_filename(name):
    """Properly sanitize filename for cross-platform compatibility"""
    # Remove or replace problematic characters
    name = re.sub(r'[\\/*?:"<>|]', '', name)
    # Replace spaces and other characters that might cause issues
    name = re.sub(r'[\s\[\](){}]', '_', name)
    # Remove multiple underscores
    name = re.sub(r'_+', '_', name)
    # Remove leading/trailing underscores and dots
    name = name.strip('_.')
    # Ensure filename isn't empty
    if not name:
        name = 'video'
    return name

def clean_up_old_files(limit=2):
    """Clean up old downloaded files"""
    files = glob.glob(os.path.join(DOWNLOAD_FOLDER, "*"))
    files = [f for f in files if os.path.isfile(f)]
    if len(files) >= limit:
        # Sort by creation time (oldest first)
        files.sort(key=lambda x: os.path.getctime(x))
        # Delete oldest files, keep only (limit-1)
        for f in files[:len(files) - (limit - 1)]:
            try:
                os.remove(f)
            except OSError:
                pass  # File might already be deleted

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
        
        # Use a simpler template to avoid filename issues
        temp_template = os.path.join(DOWNLOAD_FOLDER, f"temp_{unique_id}.%(ext)s")
        
        ydl_opts = {
            'outtmpl': temp_template,
            'format': 'best[height<=720]/best',  # Better compatibility for older Android
            'merge_output_format': 'mp4',
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
            # Better user agent for compatibility
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Linux; Android 10; SM-G973F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.120 Mobile Safari/537.36'
            },
            # Add cookies file only if it exists
            'cookiefile': 'cookies.txt' if os.path.exists('cookies.txt') else None,
            # Additional options for better compatibility
            'prefer_ffmpeg': True,
            'postprocessors': [{
                'key': 'FFmpegVideoConvertor',
                'preferedformat': 'mp4',
            }]
        }
        
        # Remove None values from ydl_opts
        ydl_opts = {k: v for k, v in ydl_opts.items() if v is not None}
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Extract info first
            info = ydl.extract_info(url, download=False)
            title = info.get('title', 'video')
            
            # Create a clean filename
            clean_title = sanitize_filename(title)
            final_filename = f"{unique_id}_{clean_title}.mp4"
            final_path = os.path.join(DOWNLOAD_FOLDER, final_filename)
            
            # Update output template with clean filename
            ydl_opts['outtmpl'] = final_path.replace('.mp4', '.%(ext)s')
            
            # Download the video
            with yt_dlp.YoutubeDL(ydl_opts) as ydl_download:
                ydl_download.download([url])
            
            # Find the actual downloaded file
            downloaded_files = glob.glob(os.path.join(DOWNLOAD_FOLDER, f"{unique_id}_*"))
            if not downloaded_files:
                return jsonify({"error": "Download failed - file not found"}), 500
            
            actual_file = downloaded_files[0]
            actual_filename = os.path.basename(actual_file)
            
            # URL encode the filename for proper URL handling
            encoded_filename = urllib.parse.quote(actual_filename, safe='')
            
            return jsonify({
                "success": True,
                "message": "Video downloaded successfully!",
                "filename": actual_filename,
                "title": title,
                "download_url": f"{request.host_url}static/downloads/{encoded_filename}"
            })
            
    except yt_dlp.DownloadError as e:
        return jsonify({"error": f"Download failed: {str(e)}"}), 500
    except Exception as e:
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500

@app.route("/static/downloads/<path:filename>")
def serve_file(filename):
    """Serve downloaded files with proper headers for Android compatibility"""
    try:
        # URL decode the filename
        decoded_filename = urllib.parse.unquote(filename)
        
        response = send_from_directory(DOWNLOAD_FOLDER, decoded_filename, as_attachment=False)
        
        # Add headers for better Android compatibility
        response.headers['Content-Type'] = 'video/mp4'
        response.headers['Accept-Ranges'] = 'bytes'
        response.headers['Cache-Control'] = 'no-cache'
        
        return response
    except FileNotFoundError:
        return jsonify({"error": "File not found"}), 404

@app.route("/api/list", methods=["GET"])
def list_files():
    """List available downloaded files"""
    try:
        files = glob.glob(os.path.join(DOWNLOAD_FOLDER, "*"))
        files = [f for f in files if os.path.isfile(f)]
        
        file_list = []
        for file_path in files:
            filename = os.path.basename(file_path)
            encoded_filename = urllib.parse.quote(filename, safe='')
            file_info = {
                "filename": filename,
                "size": os.path.getsize(file_path),
                "created": datetime.fromtimestamp(os.path.getctime(file_path)).isoformat(),
                "download_url": f"{request.host_url}static/downloads/{encoded_filename}"
            }
            file_list.append(file_info)
        
        return jsonify({
            "success": True,
            "files": file_list,
            "count": len(file_list)
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)

