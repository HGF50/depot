from flask import Flask, request, send_from_directory, jsonify, make_response
import os
import shutil
import zipfile
import json
from datetime import datetime
from io import BytesIO

USE_CLOUD = os.environ.get('CLOUDINARY_CLOUD_NAME', '')

if USE_CLOUD:
    import cloudinary
    import cloudinary.uploader
    cloudinary.config(
        cloud_name=os.environ.get('CLOUDINARY_CLOUD_NAME', ''),
        api_key=os.environ.get('CLOUDINARY_API_KEY', ''),
        api_secret=os.environ.get('CLOUDINARY_API_SECRET', ''),
        secure=True
    )

app = Flask(__name__, static_folder='.')
DIRECTORY = os.getcwd()

EXCLUDE = {'.venv', 'server.py', 'index.html', '__pycache__', '.git', '.gemini', '.pytest_cache', 'manifest.json', 'sw.js', 'icon.svg', 'metadata.json', 'mobile.html'}

METADATA_FILE = 'metadata.json'

def load_metadata():
    path = os.path.join(DIRECTORY, METADATA_FILE)
    if os.path.exists(path):
        import json
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {'files': [], 'notes': {'content': '', 'history': []}}

def save_metadata(data):
    import json
    path = os.path.join(DIRECTORY, METADATA_FILE)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

@app.route('/')
def home():
    return send_from_directory('.', 'index.html')

@app.route('/manifest.json')
def get_manifest():
    return send_from_directory('.', 'manifest.json')

@app.route('/sw.js')
def get_sw():
    return send_from_directory('.', 'sw.js'), 200, {'Content-Type': 'application/javascript'}

@app.route('/mobile')
def mobile():
    return send_from_directory('.', 'mobile.html')

@app.route('/list')
def list_files():
    sub_path = request.args.get('path', '')
    
    if USE_CLOUD:
        try:
            result = cloudinary.api.resources(type='upload', prefix='depot/' + sub_path)
            items = []
            for r in result.get('resources', []):
                items.append({
                    'name': r.get('public_id', '').split('/')[-1],
                    'date': datetime.fromtimestamp(r.get('created_at', 0)/1000).strftime('%d/%m/%Y'),
                    'size': r.get('bytes', 0),
                    'isFolder': False,
                    'path': r.get('public_id', '').replace('depot/', ''),
                    'url': r.get('secure_url', ''),
                    'uploaded': True
                })
            return jsonify(items)
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    
    target_dir = os.path.abspath(os.path.join(DIRECTORY, sub_path))
    
    if not target_dir.startswith(os.path.abspath(DIRECTORY)):
        return jsonify({'error': 'Invalid path'}), 403
    
    if not os.path.exists(target_dir):
        return jsonify({'error': 'Directory not found'}), 404

    items = []
    try:
        for entry in os.scandir(target_dir):
            if entry.name in EXCLUDE or entry.name.startswith('.'):
                continue
            
            stats = entry.stat()
            item = {
                'name': entry.name,
                'date': datetime.fromtimestamp(stats.st_mtime).strftime('%d/%m/%Y'),
                'size': stats.st_size,
                'isFolder': entry.is_dir(),
                'path': os.path.relpath(entry.path, DIRECTORY).replace('\\', '/'),
                'uploaded': True
            }
            
            if entry.is_dir():
                try:
                    count = 0
                    total_size = 0
                    for root, dirs, files in os.walk(entry.path):
                        count += len(files)
                        for f in files:
                            total_size += os.path.getsize(os.path.join(root, f))
                    item['size'] = total_size
                    item['fileCount'] = count
                except OSError:
                    item['size'] = 0
                    item['fileCount'] = 0
            
            items.append(item)
    except Exception as e:
        return jsonify({'error': str(e)}), 500
        
    return jsonify(items)

@app.route('/delete', methods=['POST'])
def delete_item():
    data = request.json
    filename = data.get('name')
    if not filename:
        return jsonify({'error': 'No name provided'}), 400
    
    full_path = os.path.join(DIRECTORY, filename)
    if not os.path.exists(full_path):
        return jsonify({'error': 'File not found'}), 404
    
    if not os.path.abspath(full_path).startswith(os.path.abspath(DIRECTORY)):
        return jsonify({'error': 'Access denied'}), 403
    
    try:
        if os.path.isdir(full_path):
            shutil.rmtree(full_path)
        else:
            os.remove(full_path)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/upload', methods=['POST'])
def upload():
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    
    files = request.files.getlist('file')
    for file in files:
        if file.filename == '':
            continue
        
        if USE_CLOUD:
            file.seek(0)
            cloudinary.uploader.upload(file, public_id=file.filename, folder="depot")
        else:
            full_path = os.path.join(DIRECTORY, file.filename)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            file.save(full_path)
         
    return jsonify({'success': True})

@app.route('/zip')
def zip_folder():
    folder_path = request.args.get('path', '')
    target_dir = os.path.abspath(os.path.join(DIRECTORY, folder_path))
    
    if not target_dir.startswith(os.path.abspath(DIRECTORY)):
        return jsonify({'error': 'Access denied'}), 403
    
    if not os.path.isdir(target_dir):
        return jsonify({'error': 'Not a folder'}), 404
    
    folder_name = os.path.basename(target_dir) or 'folder'
    
    memory_file = BytesIO()
    with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(target_dir):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, target_dir)
                zf.write(file_path, arcname)
    
    memory_file.seek(0)
    return memory_file.getvalue(), 200, {
        'Content-Type': 'application/zip',
        'Content-Disposition': f'attachment; filename={folder_name}.zip'
    }

@app.route('/notes', methods=['GET', 'POST'])
def notes():
    if request.method == 'POST':
        try:
            data = request.json or {}
            save_metadata({'files': load_metadata().get('files', []), 'notes': data})
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    else:
        try:
            metadata = load_metadata()
            return jsonify(metadata.get('notes', {'content': '', 'history': []}))
        except:
            return jsonify({'content': '', 'history': []})

@app.route('/<path:path>')
def serve_static(path):
    return send_from_directory('.', path)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000, debug=True)