from flask import Flask, request, jsonify, send_from_directory
import os
import yt_dlp
import uuid
import re
import glob
import urllib.parse
import threading
import time
from datetime import datetime
import subprocess
import sys

app = Flask(__name__)
DOWNLOAD_FOLDER = os.path.join("static", "downloads")
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

# Global variable to track download status
download_status = {}

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

def clean_up_old_files(limit=3):
    """Clean up old downloaded files"""
    try:
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
    except Exception:
        pass  # Ignore cleanup errors

def download_video_async(url, unique_id, download_id):
    """Async download function to prevent worker timeout"""
    try:
        download_status[download_id] = {
            'status': 'downloading',
            'progress': 0,
            'filename': None,
            'title': None,
            'error': None
        }
        
        # Clean up old files before downloading
        clean_up_old_files(limit=3)
        
        # More robust yt-dlp options
        ydl_opts = {
            'format': 'best[height<=480]/best[height<=720]/best',  # Lower quality for faster download
            'merge_output_format': 'mp4',
            'outtmpl': os.path.join(DOWNLOAD_FOLDER, f'{unique_id}_%(title)s.%(ext)s'),
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
            'ignoreerrors': True,
            'no_check_certificate': True,
            'prefer_insecure': True,
            'socket_timeout': 30,
            'retries': 3,
            'fragment_retries': 3,
            'http_chunk_size': 10485760,  # 10MB chunks
            'extractor_args': {
                'youtube': {
                    'player_client': ['android', 'web'],
                    'player_skip': ['configs', 'webpage'],
                    'skip': ['dash', 'hls']
                }
            },
            'http_headers': {
                'User-Agent': 'com.google.android.youtube/17.36.4 (Linux; U; Android 12; GB) gzip',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-us,en;q=0.5',
                'Sec-Fetch-Mode': 'navigate',
            }
        }
        
        # Add cookies file only if it exists
        if os.path.exists('cookies.txt'):
            ydl_opts['cookiefile'] = 'cookies.txt'
        
        # Progress hook
        def progress_hook(d):
            if d['status'] == 'downloading':
                try:
                    percent = d.get('_percent_str', '0%').replace('%', '')
                    download_status[download_id]['progress'] = float(percent)
                except:
                    pass
            elif d['status'] == 'finished':
                download_status[download_id]['progress'] = 100
        
        ydl_opts['progress_hooks'] = [progress_hook]
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Extract info first
            info = ydl.extract_info(url, download=False)
            title = info.get('title', 'video')
            
            # Update status
            download_status[download_id]['title'] = title
            
            # Download the video
            ydl.download([url])
            
            # Find the downloaded file
            pattern = os.path.join(DOWNLOAD_FOLDER, f"{unique_id}_*")
            downloaded_files = glob.glob(pattern)
            
            if downloaded_files:
                actual_file = downloaded_files[0]
                actual_filename = os.path.basename(actual_file)
                
                download_status[download_id].update({
                    'status': 'completed',
                    'progress': 100,
                    'filename': actual_filename,
                    'title': title
                })
            else:
                download_status[download_id].update({
                    'status': 'error',
                    'error': 'Download completed but file not found'
                })
                
    except yt_dlp.DownloadError as e:
        error_msg = str(e)
        # Provide more user-friendly error messages
        if 'Video unavailable' in error_msg:
            error_msg = 'Video is unavailable or private'
        elif 'Sign in to confirm your age' in error_msg:
            error_msg = 'Video requires age verification'
        elif 'Private video' in error_msg:
            error_msg = 'Video is private'
        
        download_status[download_id].update({
            'status': 'error',
            'error': error_msg
        })
    except Exception as e:
        download_status[download_id].update({
            'status': 'error',
            'error': f'Download failed: {str(e)}'
        })

@app.route("/")
def home():
    return "🎬 Lucid Video API by Ram Sharma is running!"

@app.route("/api/download", methods=["POST"])
def download_video():
    data = request.get_json()
    url = data.get("url")
    
    if not url:
        return jsonify({"error": "No URL provided."}), 400
    
    # Generate unique IDs
    unique_id = str(uuid.uuid4())[:8]
    download_id = str(uuid.uuid4())
    
    # Start async download
    thread = threading.Thread(
        target=download_video_async, 
        args=(url, unique_id, download_id)
    )
    thread.daemon = True
    thread.start()
    
    return jsonify({
        "success": True,
        "message": "Download started",
        "download_id": download_id,
        "status_url": f"{request.host_url}api/status/{download_id}"
    })

@app.route("/api/status/<download_id>", methods=["GET"])
def get_download_status(download_id):
    """Get download status"""
    if download_id not in download_status:
        return jsonify({"error": "Download ID not found"}), 404
    
    status = download_status[download_id].copy()
    
    # Add download URL if completed
    if status['status'] == 'completed' and status['filename']:
        encoded_filename = urllib.parse.quote(status['filename'], safe='')
        status['download_url'] = f"{request.host_url}static/downloads/{encoded_filename}"
    
    return jsonify(status)

@app.route("/api/download-sync", methods=["POST"])
def download_video_sync():
    """Synchronous download for smaller videos - use with caution"""
    data = request.get_json()
    url = data.get("url")
    
    if not url:
        return jsonify({"error": "No URL provided."}), 400
    
    try:
        unique_id = str(uuid.uuid4())[:8]
        
        # Very restrictive options for sync download
        ydl_opts = {
            'format': 'worst[height<=360]/worst',  # Very low quality for speed
            'outtmpl': os.path.join(DOWNLOAD_FOLDER, f'{unique_id}_%(title)s.%(ext)s'),
            'quiet': True,
            'no_warnings': True,
            'socket_timeout': 15,
            'retries': 1,
            'extractor_args': {
                'youtube': {
                    'player_client': ['android'],
                    'skip': ['dash', 'hls']
                }
            },
            'http_headers': {
                'User-Agent': 'com.google.android.youtube/17.36.4 (Linux; U; Android 12; GB) gzip'
            }
        }
        
        # Add cookies if available
        if os.path.exists('cookies.txt'):
            ydl_opts['cookiefile'] = 'cookies.txt'
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            # Check duration - only allow short videos for sync download
            duration = info.get('duration', 0)
            if duration and duration > 300:  # 5 minutes
                return jsonify({
                    "error": "Video too long for sync download. Use async endpoint instead.",
                    "duration": duration
                }), 400
            
            title = info.get('title', 'video')
            ydl.download([url])
            
            # Find downloaded file
            pattern = os.path.join(DOWNLOAD_FOLDER, f"{unique_id}_*")
            downloaded_files = glob.glob(pattern)
            
            if downloaded_files:
                actual_file = downloaded_files[0]
                actual_filename = os.path.basename(actual_file)
                encoded_filename = urllib.parse.quote(actual_filename, safe='')
                
                return jsonify({
                    "success": True,
                    "message": "Video downloaded successfully!",
                    "filename": actual_filename,
                    "title": title,
                    "download_url": f"{request.host_url}static/downloads/{encoded_filename}"
                })
            else:
                return jsonify({"error": "Download completed but file not found"}), 500
                
    except Exception as e:
        return jsonify({"error": f"Download failed: {str(e)}"}), 500

@app.route("/static/downloads/<path:filename>")
def serve_file(filename):
    """Serve downloaded files with proper headers"""
    try:
        decoded_filename = urllib.parse.unquote(filename)
        
        response = send_from_directory(DOWNLOAD_FOLDER, decoded_filename, as_attachment=False)
        
        # Add headers for better compatibility
        response.headers['Content-Type'] = 'video/mp4'
        response.headers['Accept-Ranges'] = 'bytes'
        response.headers['Cache-Control'] = 'public, max-age=3600'
        
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

@app.route("/api/cleanup", methods=["POST"])
def cleanup_files():
    """Manual cleanup endpoint"""
    try:
        files = glob.glob(os.path.join(DOWNLOAD_FOLDER, "*"))
        deleted_count = 0
        
        for file_path in files:
            try:
                os.remove(file_path)
                deleted_count += 1
            except OSError:
                pass
        
        return jsonify({
            "success": True,
            "message": f"Deleted {deleted_count} files"
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Clean up old download status entries periodically
def cleanup_status():
    """Clean up old status entries"""
    while True:
        try:
            current_time = time.time()
            to_remove = []
            
            for download_id, status in download_status.items():
                # Remove status entries older than 1 hour
                if current_time - status.get('timestamp', current_time) > 3600:
                    to_remove.append(download_id)
            
            for download_id in to_remove:
                download_status.pop(download_id, None)
                
        except Exception:
            pass
        
        time.sleep(1800)  # Run every 30 minutes

# Start cleanup thread
cleanup_thread = threading.Thread(target=cleanup_status)
cleanup_thread.daemon = True
cleanup_thread.start()

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
