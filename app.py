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
import gc
import psutil

from flask_cors import CORS

# After app = Flask(__name__)
CORS(app, origins=["*"])  # Or specify your domain

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

def get_disk_usage():
    """Get current disk usage information"""
    try:
        usage = psutil.disk_usage('/')
        return {
            'total': usage.total,
            'used': usage.used,
            'free': usage.free,
            'percent': (usage.used / usage.total) * 100
        }
    except:
        return None

def aggressive_cleanup():
    """Aggressively clean up all files and force garbage collection"""
    try:
        print("[CLEANUP] Starting aggressive cleanup...")
        
        # Get disk usage before cleanup
        disk_before = get_disk_usage()
        if disk_before:
            print(f"[CLEANUP] Disk usage before: {disk_before['percent']:.1f}% ({disk_before['free']/(1024**3):.2f}GB free)")
        
        # Remove all files in download folder
        files = glob.glob(os.path.join(DOWNLOAD_FOLDER, "*"))
        deleted_count = 0
        total_size_freed = 0
        
        for file_path in files:
            try:
                if os.path.isfile(file_path):
                    file_size = os.path.getsize(file_path)
                    os.remove(file_path)
                    deleted_count += 1
                    total_size_freed += file_size
                    print(f"[CLEANUP] Deleted: {os.path.basename(file_path)} ({file_size/(1024**2):.1f}MB)")
            except OSError as e:
                print(f"[CLEANUP] Failed to delete {file_path}: {e}")
        
        # Clean up old status entries
        old_status_count = len(download_status)
        download_status.clear()
        
        # Force garbage collection
        gc.collect()
        
        # Get disk usage after cleanup
        disk_after = get_disk_usage()
        if disk_after:
            print(f"[CLEANUP] Disk usage after: {disk_after['percent']:.1f}% ({disk_after['free']/(1024**3):.2f}GB free)")
        
        print(f"[CLEANUP] Completed: {deleted_count} files deleted, {total_size_freed/(1024**2):.1f}MB freed, {old_status_count} status entries cleared")
        
        return {
            'files_deleted': deleted_count,
            'size_freed': total_size_freed,
            'status_cleared': old_status_count
        }
        
    except Exception as e:
        print(f"[CLEANUP] Error during cleanup: {e}")
        return {'error': str(e)}

def clean_up_old_files(limit=1):
    """Clean up old downloaded files - called only when new download is requested"""
    try:
        files = glob.glob(os.path.join(DOWNLOAD_FOLDER, "*"))
        files = [f for f in files if os.path.isfile(f)]
        
        if len(files) >= limit:
            # Sort by creation time (oldest first)
            files.sort(key=lambda x: os.path.getctime(x))
            # Delete old files to make room for new download
            files_to_delete = files[:-1] if limit > 0 else files
            for f in files_to_delete:
                try:
                    file_size = os.path.getsize(f)
                    os.remove(f)
                    print(f"[CLEANUP] Deleted old file: {os.path.basename(f)} ({file_size/(1024**2):.1f}MB)")
                except OSError as e:
                    print(f"[CLEANUP] Failed to delete {f}: {e}")
        
        # Force garbage collection after cleanup
        gc.collect()
        
    except Exception as e:
        print(f"[CLEANUP] Error in cleanup: {e}")

def download_video_async(url, unique_id, download_id):
    """Async download function - cleanup is done before calling this function"""
    try:
        download_status[download_id] = {
            'status': 'downloading',
            'progress': 0,
            'filename': None,
            'title': None,
            'error': None,
            'timestamp': time.time()
        }
        
        print(f"[DOWNLOAD] Starting download for ID: {download_id}")
        
        # Check disk space (cleanup already done in the API endpoint)
        disk_info = get_disk_usage()
        if disk_info and disk_info['percent'] > 85:
            raise Exception(f"Insufficient disk space: {disk_info['percent']:.1f}% used")
        
        # More restrictive yt-dlp options for resource conservation
        ydl_opts = {
            'format': 'worst[height<=360]/worst[height<=480]/worst',  # Very low quality
            'merge_output_format': 'mp4',
            'outtmpl': os.path.join(DOWNLOAD_FOLDER, f'{unique_id}_%(title)s.%(ext)s'),
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
            'ignoreerrors': True,
            'no_check_certificate': True,
            'prefer_insecure': True,
            'socket_timeout': 20,
            'retries': 2,
            'fragment_retries': 2,
            'http_chunk_size': 5242880,  # 5MB chunks (smaller)
            'concurrent_fragment_downloads': 1,  # Reduce concurrent downloads
            'extractor_args': {
                'youtube': {
                    'player_client': ['android'],
                    'player_skip': ['configs', 'webpage'],
                    'skip': ['dash', 'hls']
                }
            },
            'http_headers': {
                'User-Agent': 'com.google.android.youtube/17.36.4 (Linux; U; Android 12; GB) gzip',
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
                    
                    # Log progress periodically
                    if int(float(percent)) % 25 == 0:
                        print(f"[DOWNLOAD] Progress: {percent}%")
                        
                except:
                    pass
            elif d['status'] == 'finished':
                download_status[download_id]['progress'] = 100
                print("[DOWNLOAD] Download finished, processing...")
        
        ydl_opts['progress_hooks'] = [progress_hook]
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Extract info first
            print("[DOWNLOAD] Extracting video info...")
            info = ydl.extract_info(url, download=False)
            title = info.get('title', 'video')
            duration = info.get('duration', 0)
            
            # Limit duration to prevent large downloads
            if duration and duration > 600:  # 10 minutes max
                raise Exception(f"Video too long ({duration}s). Maximum allowed: 600s")
            
            # Update status
            download_status[download_id]['title'] = title
            print(f"[DOWNLOAD] Downloading: {title} ({duration}s)")
            
            # Download the video
            ydl.download([url])
            
            # Find the downloaded file
            pattern = os.path.join(DOWNLOAD_FOLDER, f"{unique_id}_*")
            downloaded_files = glob.glob(pattern)
            
            if downloaded_files:
                actual_file = downloaded_files[0]
                actual_filename = os.path.basename(actual_file)
                file_size = os.path.getsize(actual_file)
                
                print(f"[DOWNLOAD] Download completed: {actual_filename} ({file_size/(1024**2):.1f}MB)")
                
                download_status[download_id].update({
                    'status': 'completed',
                    'progress': 100,
                    'filename': actual_filename,
                    'title': title,
                    'file_size': file_size
                })
                
                print(f"[DOWNLOAD] Successfully completed: {actual_filename}")
                
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
        
        print(f"[DOWNLOAD] yt-dlp error: {error_msg}")
        download_status[download_id].update({
            'status': 'error',
            'error': error_msg
        })
    except Exception as e:
        print(f"[DOWNLOAD] General error: {str(e)}")
        download_status[download_id].update({
            'status': 'error',
            'error': f'Download failed: {str(e)}'
        })
    finally:
        # Light garbage collection only
        gc.collect()

@app.route("/")
def home():
    disk_info = get_disk_usage()
    disk_text = f" | Disk: {disk_info['percent']:.1f}% used" if disk_info else ""
    return f"🎬 Lucid Video API by Ram Sharma is running!{disk_text}"

@app.route("/api/download", methods=["POST"])
def download_video():
    data = request.get_json()
    url = data.get("url")
    
    if not url:
        return jsonify({"error": "No URL provided."}), 400
    
    # Perform aggressive cleanup before starting any new download
    print("[API] New download request received, performing cleanup...")
    cleanup_result = aggressive_cleanup()
    
    # Check disk space after cleanup
    disk_info = get_disk_usage()
    if disk_info and disk_info['percent'] > 90:
        return jsonify({
            "error": f"Insufficient disk space: {disk_info['percent']:.1f}% used. Please try again later."
        }), 507
    
    try:
        # First, try to get video info to determine if we should use sync or async
        print("[API] Checking video info to determine download method...")
        
        ydl_opts_info = {
            'quiet': True,
            'no_warnings': True,
            'socket_timeout': 10,
            'retries': 1,
            'extractor_args': {
                'youtube': {
                    'player_client': ['android'],
                    'skip': ['dash', 'hls']
                }
            }
        }
        
        with yt_dlp.YoutubeDL(ydl_opts_info) as ydl:
            info = ydl.extract_info(url, download=False)
            duration = info.get('duration', 0)
            title = info.get('title', 'video')
            
            print(f"[API] Video info: {title} - Duration: {duration}s")
            
            # If video is short (under 3 minutes), do sync download
            if duration and duration <= 180:
                print("[API] Short video detected, using sync download...")
                return handle_sync_download(url, info, title, duration, cleanup_result)
            else:
                print("[API] Long video detected, using async download...")
                return handle_async_download(url, info, title, duration, cleanup_result)
                
    except Exception as e:
        print(f"[API] Error getting video info: {e}")
        # If we can't get info, fall back to async
        print("[API] Falling back to async download...")
        return handle_async_download(url, None, None, None, cleanup_result)

def handle_sync_download(url, info, title, duration, cleanup_result):
    """Handle synchronous download for short videos"""
    try:
        print("[SYNC] Starting synchronous download...")
        unique_id = str(uuid.uuid4())[:8]
        
        # Very restrictive options for sync download
        ydl_opts = {
            'format': 'worst[height<=360]/worst',  # Low quality for speed
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
            print(f"[SYNC] Downloading: {title}")
            ydl.download([url])
            
            # Find downloaded file
            pattern = os.path.join(DOWNLOAD_FOLDER, f"{unique_id}_*")
            downloaded_files = glob.glob(pattern)
            
            if downloaded_files:
                actual_file = downloaded_files[0]
                actual_filename = os.path.basename(actual_file)
                file_size = os.path.getsize(actual_file)
                encoded_filename = urllib.parse.quote(actual_filename, safe='')
                
                print(f"[SYNC] Download completed: {actual_filename} ({file_size/(1024**2):.1f}MB)")
                
                # Return immediate download URL (what frontend expects)
                return jsonify({
                    "success": True,
                    "message": "Video downloaded successfully!",
                    "filename": actual_filename,
                    "title": title,
                    "file_size": file_size,
                    "download_url": f"{request.host_url}static/downloads/{encoded_filename}",
                    "method": "sync",
                    "cleanup_performed": cleanup_result
                })
            else:
                raise Exception("Download completed but file not found")
                
    except Exception as e:
        print(f"[SYNC] Sync download failed: {e}")
        # If sync fails, fall back to async
        print("[SYNC] Falling back to async download...")
        return handle_async_download(url, info, title, duration, cleanup_result)
    finally:
        gc.collect()

def handle_async_download(url, info, title, duration, cleanup_result):
    """Handle asynchronous download for long videos or when sync fails"""
    try:
        # Generate unique IDs
        unique_id = str(uuid.uuid4())[:8]
        download_id = str(uuid.uuid4())
        
        print(f"[ASYNC] Starting async download with ID: {download_id}")
        
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
            "status_url": f"{request.host_url}api/status/{download_id}",
            "method": "async",
            "title": title,
            "duration": duration,
            "cleanup_performed": cleanup_result
        })
        
    except Exception as e:
        print(f"[ASYNC] Failed to start async download: {e}")
        return jsonify({"error": f"Failed to start download: {str(e)}"}), 500


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
    
    # Add disk usage info
    disk_info = get_disk_usage()
    if disk_info:
        status['disk_usage'] = f"{disk_info['percent']:.1f}%"
    
    return jsonify(status)

@app.route("/api/download-sync", methods=["POST"])
def download_video_sync():
    """Synchronous download for very small videos only"""
    data = request.get_json()
    url = data.get("url")
    
    if not url:
        return jsonify({"error": "No URL provided."}), 400
    
    try:
        # Aggressive cleanup ONLY when new sync download is requested
        print("[SYNC] Sync download request, performing cleanup...")
        aggressive_cleanup()
        
        unique_id = str(uuid.uuid4())[:8]
        
        # Very restrictive options for sync download
        ydl_opts = {
            'format': 'worst[height<=240]/worst',  # Extremely low quality
            'outtmpl': os.path.join(DOWNLOAD_FOLDER, f'{unique_id}_%(title)s.%(ext)s'),
            'quiet': True,
            'no_warnings': True,
            'socket_timeout': 10,
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
            
            # Check duration - only allow very short videos for sync download
            duration = info.get('duration', 0)
            if duration and duration > 180:  # 3 minutes max for sync
                return jsonify({
                    "error": "Video too long for sync download. Use async endpoint instead.",
                    "duration": duration,
                    "max_duration": 180
                }), 400
            
            title = info.get('title', 'video')
            ydl.download([url])
            
            # Find downloaded file
            pattern = os.path.join(DOWNLOAD_FOLDER, f"{unique_id}_*")
            downloaded_files = glob.glob(pattern)
            
            if downloaded_files:
                actual_file = downloaded_files[0]
                actual_filename = os.path.basename(actual_file)
                file_size = os.path.getsize(actual_file)
                encoded_filename = urllib.parse.quote(actual_filename, safe='')
                
                # No immediate cleanup - cleanup happens on next request
                print(f"[SYNC] Download completed successfully: {actual_filename}")
                
                return jsonify({
                    "success": True,
                    "message": "Video downloaded successfully!",
                    "filename": actual_filename,
                    "title": title,
                    "file_size": file_size,
                    "download_url": f"{request.host_url}static/downloads/{encoded_filename}"
                })
            else:
                return jsonify({"error": "Download completed but file not found"}), 500
                
    except Exception as e:
        return jsonify({"error": f"Download failed: {str(e)}"}), 500
    finally:
        gc.collect()

@app.route("/static/downloads/<path:filename>")
def serve_file(filename):
    try:
        decoded_filename = urllib.parse.unquote(filename)
        response = send_from_directory(
            DOWNLOAD_FOLDER, 
            decoded_filename, 
            as_attachment=True
        )
        
        # Add CORS headers
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Content-Disposition'] = f'attachment; filename="{decoded_filename}"'
        
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
        total_size = 0
        for file_path in files:
            filename = os.path.basename(file_path)
            file_size = os.path.getsize(file_path)
            total_size += file_size
            encoded_filename = urllib.parse.quote(filename, safe='')
            file_info = {
                "filename": filename,
                "size": file_size,
                "size_mb": round(file_size / (1024**2), 2),
                "created": datetime.fromtimestamp(os.path.getctime(file_path)).isoformat(),
                "download_url": f"{request.host_url}static/downloads/{encoded_filename}"
            }
            file_list.append(file_info)
        
        # Get disk usage
        disk_info = get_disk_usage()
        
        return jsonify({
            "success": True,
            "files": file_list,
            "count": len(file_list),
            "total_size_mb": round(total_size / (1024**2), 2),
            "disk_usage": disk_info
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/cleanup", methods=["POST"])
def cleanup_files():
    """Manual cleanup endpoint"""
    try:
        result = aggressive_cleanup()
        return jsonify({
            "success": True,
            "message": f"Cleanup completed: {result.get('files_deleted', 0)} files deleted",
            "details": result
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/health", methods=["GET"])
def health_check():
    """Health check endpoint with system info"""
    try:
        disk_info = get_disk_usage()
        file_count = len([f for f in glob.glob(os.path.join(DOWNLOAD_FOLDER, "*")) if os.path.isfile(f)])
        
        return jsonify({
            "status": "healthy",
            "disk_usage": disk_info,
            "files_count": file_count,
            "download_status_count": len(download_status),
            "timestamp": datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

# Clean up old download status entries periodically
def cleanup_status():
    """Clean up old status entries"""
    while True:
        try:
            current_time = time.time()
            to_remove = []
            
            for download_id, status in download_status.items():
                # Remove status entries older than 30 minutes (shorter)
                if current_time - status.get('timestamp', current_time) > 1800:
                    to_remove.append(download_id)
            
            for download_id in to_remove:
                download_status.pop(download_id, None)
            
            if to_remove:
                print(f"[CLEANUP] Removed {len(to_remove)} old status entries")
            
            # Periodic aggressive cleanup every hour
            if int(current_time) % 3600 < 60:  # Once per hour
                print("[CLEANUP] Performing scheduled cleanup...")
                aggressive_cleanup()
                
        except Exception as e:
            print(f"[CLEANUP] Error in status cleanup: {e}")
        
        time.sleep(600)  # Run every 10 minutes (more frequent)

# Start cleanup thread
cleanup_thread = threading.Thread(target=cleanup_status)
cleanup_thread.daemon = True
cleanup_thread.start()

if __name__ == "__main__":
    print("[STARTUP] Starting Flask app with aggressive cleanup...")
    # Initial cleanup on startup
    aggressive_cleanup()
    app.run(debug=True, host="0.0.0.0", port=5000)