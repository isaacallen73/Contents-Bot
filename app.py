import json
import uuid
import sys
import threading
import time
import queue
import socket
import webbrowser
import secrets as _secrets_mod
from functools import wraps
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from flask import (Flask, request, jsonify, render_template, send_file,
                   Response, stream_with_context,
                   session as flask_session, redirect, url_for, g)
from authlib.integrations.flask_client import OAuth

# ── Paths — handle PyInstaller --onefile bundle ───────────────────────────────
if getattr(sys, 'frozen', False):
    BUNDLE_DIR = Path(sys._MEIPASS)   # bundled app files (templates, static)
    BASE_DIR   = Path(sys.executable).parent  # writable data dir next to .exe
else:
    BUNDLE_DIR = Path(__file__).parent
    BASE_DIR   = Path(__file__).parent

app = Flask(__name__,
    template_folder=str(BUNDLE_DIR / 'templates'),
    static_folder=str(BUNDLE_DIR / 'static'))
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024

SESSIONS_DIR  = BASE_DIR / 'sessions'
CONFIG_FILE   = BASE_DIR / 'config.json'
FEEDBACK_DIR  = BASE_DIR / 'feedback'

SESSIONS_DIR.mkdir(exist_ok=True)
FEEDBACK_DIR.mkdir(exist_ok=True)

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.heic', '.heif', '.webp', '.bmp', '.tiff', '.tif'}

ALLOWED_DOMAIN = 'liberty-restoration.com'
USAGE_DIR      = BASE_DIR / 'usage'
USAGE_DIR.mkdir(exist_ok=True)
_usage_lock    = threading.Lock()
_feedback_lock = threading.Lock()

# ── Persistent secret key ─────────────────────────────────────────────────────

_init_cfg = json.loads(CONFIG_FILE.read_text(encoding='utf-8')) if CONFIG_FILE.exists() else {}
if not _init_cfg.get('_secret_key'):
    _init_cfg['_secret_key'] = _secrets_mod.token_hex(32)
    CONFIG_FILE.write_text(json.dumps(_init_cfg, indent=2), encoding='utf-8')
app.secret_key = _init_cfg['_secret_key']

# ── OAuth ─────────────────────────────────────────────────────────────────────

oauth = OAuth(app)
_google_registered = False

def _ensure_google_oauth():
    global _google_registered
    if _google_registered:
        return True
    cfg = load_config()
    cid  = cfg.get('google_oauth_client_id', '')
    csec = cfg.get('google_oauth_client_secret', '')
    if not cid or not csec:
        return False
    oauth.register(
        name='google',
        client_id=cid,
        client_secret=csec,
        server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
        client_kwargs={'scope': 'openid email profile'},
    )
    _google_registered = True
    return True

# ── Config ────────────────────────────────────────────────────────────────────

def load_config():
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text(encoding='utf-8'))
    return {}

def save_config(data):
    CONFIG_FILE.write_text(json.dumps(data, indent=2), encoding='utf-8')

# ── Usage logging ─────────────────────────────────────────────────────────────

def log_usage(email, action, **details):
    entry = {'timestamp': datetime.now().isoformat(), 'email': email, 'action': action}
    entry.update(details)
    with _usage_lock:
        with open(USAGE_DIR / 'usage_log.jsonl', 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry) + '\n')

# ── Auth ──────────────────────────────────────────────────────────────────────

def require_login(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user = flask_session.get('user')
        if not user:
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Not authenticated'}), 401
            return redirect(url_for('login_page'))
        g.current_user = user
        return f(*args, **kwargs)
    return decorated

@app.route('/login')
def login_page():
    if flask_session.get('user'):
        return redirect('/')
    return render_template('login.html')

@app.route('/login/google')
def login_google():
    if not _ensure_google_oauth():
        return render_template('login.html',
            error='Google OAuth is not configured. Ask your administrator to add '
                  'google_oauth_client_id and google_oauth_client_secret to config.json.')
    redirect_uri = url_for('auth_callback', _external=True)
    return oauth.google.authorize_redirect(redirect_uri)

@app.route('/auth/callback')
def auth_callback():
    if not _ensure_google_oauth():
        return redirect(url_for('login_page'))
    try:
        token = oauth.google.authorize_access_token()
    except Exception as e:
        return render_template('login.html', error=f'OAuth error: {e}')

    user_info = token.get('userinfo', {})
    email = user_info.get('email', '')

    if not email.lower().endswith(f'@{ALLOWED_DOMAIN}'):
        return render_template('login.html',
            error=f'Access denied. Only @{ALLOWED_DOMAIN} accounts are allowed. '
                  f'You signed in as {email}.')

    flask_session['user'] = {
        'email': email,
        'name':  user_info.get('name', email.split('@')[0]),
        'picture': user_info.get('picture', ''),
    }
    log_usage(email, 'login', name=user_info.get('name', ''))
    return redirect('/')

@app.route('/logout')
def logout():
    flask_session.pop('user', None)
    return redirect(url_for('login_page'))

# ── Config API ────────────────────────────────────────────────────────────────

@app.route('/api/config', methods=['GET'])
@require_login
def get_config():
    from processor.claude_vision import DEFAULT_MODEL, AVAILABLE_MODELS
    cfg = load_config()
    return jsonify({
        'has_anthropic_key': bool(cfg.get('anthropic_api_key')),
        'model': cfg.get('model', DEFAULT_MODEL),
        'available_models': AVAILABLE_MODELS,
    })

@app.route('/api/config', methods=['POST'])
@require_login
def set_config():
    data = request.json or {}
    cfg = load_config()
    for field in ('anthropic_api_key', 'google_search_api_key', 'google_search_cx', 'model'):
        if field in data and data[field]:
            cfg[field] = data[field]
    save_config(cfg)
    return jsonify({'ok': True})

# ── Current user ──────────────────────────────────────────────────────────────

@app.route('/api/me')
@require_login
def get_me():
    return jsonify(flask_session.get('user', {}))

# ── Usage (admin view) ────────────────────────────────────────────────────────

@app.route('/api/usage')
@require_login
def get_usage():
    log_path = USAGE_DIR / 'usage_log.jsonl'
    if not log_path.exists():
        return jsonify({'entries': [], 'summary': {}})

    entries = []
    with _usage_lock:
        for line in log_path.read_text(encoding='utf-8').splitlines():
            try:
                entries.append(json.loads(line))
            except Exception:
                pass

    summary = {}
    for e in entries:
        em = e.get('email', 'unknown')
        s = summary.setdefault(em, {'logins': 0, 'sessions': 0, 'items_processed': 0, 'exports': 0})
        a = e.get('action', '')
        if a == 'login':               s['logins'] += 1
        elif a == 'session_created':   s['sessions'] += 1
        elif a == 'processing_completed': s['items_processed'] += e.get('item_count', 0)
        elif a == 'export':            s['exports'] += 1

    return jsonify({'entries': entries[-500:], 'summary': summary})

# ── Native dialogs ────────────────────────────────────────────────────────────

@app.route('/api/browse-folder', methods=['POST'])
@require_login
def browse_folder():
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.wm_attributes('-topmost', 1)
        folder = filedialog.askdirectory(parent=root, title='Select Photo Folder')
        root.destroy()
        return jsonify({'path': folder or None})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/browse-save', methods=['POST'])
@require_login
def browse_save():
    try:
        import tkinter as tk
        from tkinter import filedialog
        data = request.json or {}
        root = tk.Tk()
        root.withdraw()
        root.wm_attributes('-topmost', 1)
        path = filedialog.asksaveasfilename(
            parent=root,
            title='Save Inventory',
            defaultextension='.xlsx',
            filetypes=[('Excel files', '*.xlsx')],
            initialdir=data.get('default_dir', ''),
            initialfile=data.get('default_name', 'Inventory.xlsx'),
        )
        root.destroy()
        return jsonify({'path': path or None})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ── Sessions ──────────────────────────────────────────────────────────────────

def load_session(session_id):
    path = SESSIONS_DIR / session_id / 'session.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None

def save_session(session_id, data):
    d = SESSIONS_DIR / session_id
    d.mkdir(exist_ok=True)
    (d / 'session.json').write_text(json.dumps(data, indent=2), encoding='utf-8')

def load_items(session_id):
    path = SESSIONS_DIR / session_id / 'items.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else []

def save_items(session_id, items):
    path = SESSIONS_DIR / session_id / 'items.json'
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(items, indent=2), encoding='utf-8')

@app.route('/api/sessions', methods=['GET'])
@require_login
def list_sessions():
    sessions = []
    if SESSIONS_DIR.exists():
        dirs = sorted(SESSIONS_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True)
        for d in dirs:
            sf = d / 'session.json'
            if sf.exists():
                try:
                    s = json.loads(sf.read_text(encoding='utf-8'))
                    items = load_items(s['session_id'])
                    sessions.append({
                        'session_id': s['session_id'],
                        'folder_path': s.get('folder_path', ''),
                        'folder_name': Path(s.get('folder_path', '')).name,
                        'status': s.get('status', 'unknown'),
                        'item_count': len(items),
                        'photo_count': len(s.get('photos', [])),
                        'created_at': s.get('created_at', ''),
                        'created_by': s.get('created_by', ''),
                    })
                except Exception:
                    pass
    return jsonify(sessions[:10])

@app.route('/api/sessions', methods=['POST'])
@require_login
def create_session():
    data = request.json or {}
    folder_path = data.get('folder_path', '').strip()
    if not folder_path or not Path(folder_path).exists():
        return jsonify({'error': 'Invalid folder path'}), 400

    folder = Path(folder_path)
    photos = sorted([
        f.name for f in folder.iterdir()
        if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS
    ])
    if not photos:
        return jsonify({'error': 'No photos found in that folder'}), 400

    session_id = datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + str(uuid.uuid4())[:4]
    user_email = g.current_user.get('email', 'unknown')
    session = {
        'session_id': session_id,
        'folder_path': folder_path,
        'photos': photos,
        'groups': [[p] for p in photos],
        'status': 'grouping',
        'created_at': datetime.now().isoformat(),
        'updated_at': datetime.now().isoformat(),
        'created_by': user_email,
    }
    save_session(session_id, session)
    log_usage(user_email, 'session_created',
              session_id=session_id, folder=folder.name, photo_count=len(photos))
    return jsonify(session)

@app.route('/api/sessions/<session_id>', methods=['GET'])
@require_login
def get_session(session_id):
    session = load_session(session_id)
    if not session:
        return jsonify({'error': 'Session not found'}), 404
    items = load_items(session_id)
    return jsonify({**session, 'items': items})

@app.route('/api/sessions/<session_id>/groups', methods=['POST'])
@require_login
def save_groups(session_id):
    session = load_session(session_id)
    if not session:
        return jsonify({'error': 'Session not found'}), 404
    session['groups'] = request.json.get('groups', [])
    session['status'] = 'processing'
    session['updated_at'] = datetime.now().isoformat()
    save_session(session_id, session)
    return jsonify({'ok': True})

# ── Photo serving ─────────────────────────────────────────────────────────────

@app.route('/api/sessions/<session_id>/thumb/<path:filename>')
@require_login
def get_thumbnail(session_id, filename):
    session = load_session(session_id)
    if not session:
        return 'Session not found', 404
    original = Path(session['folder_path']) / filename
    if not original.exists():
        return 'Photo not found', 404

    thumb_dir = SESSIONS_DIR / session_id / 'thumbs'
    thumb_dir.mkdir(exist_ok=True)
    thumb_path = thumb_dir / (Path(filename).stem + '.jpg')

    if not thumb_path.exists():
        try:
            from PIL import Image, ImageOps
            try:
                from pillow_heif import register_heif_opener
                register_heif_opener()
            except ImportError:
                pass
            img = Image.open(original)
            img = ImageOps.exif_transpose(img)
            img.thumbnail((280, 280), Image.LANCZOS)
            img = img.convert('RGB')
            img.save(thumb_path, 'JPEG', quality=82)
        except Exception:
            return send_file(original)

    return send_file(thumb_path, mimetype='image/jpeg')

@app.route('/api/sessions/<session_id>/photo/<path:filename>')
@require_login
def get_photo(session_id, filename):
    session = load_session(session_id)
    if not session:
        return 'Session not found', 404
    photo = Path(session['folder_path']) / filename
    if not photo.exists():
        return 'Photo not found', 404
    return send_file(photo)

# ── Processing ────────────────────────────────────────────────────────────────

processing_queues = {}

@app.route('/api/sessions/<session_id>/process', methods=['POST'])
@require_login
def start_processing(session_id):
    session = load_session(session_id)
    if not session:
        return jsonify({'error': 'Session not found'}), 404
    cfg = load_config()
    if not cfg.get('anthropic_api_key'):
        return jsonify({'error': 'Anthropic API key not configured'}), 400

    user_email = g.current_user.get('email', 'unknown')
    log_usage(user_email, 'processing_started',
              session_id=session_id, group_count=len(session.get('groups', [])))

    q = queue.Queue()
    processing_queues[session_id] = q
    thread = threading.Thread(
        target=_process_session, args=(session_id, cfg, q, user_email), daemon=True
    )
    thread.start()
    return jsonify({'ok': True})

@app.route('/api/sessions/<session_id>/progress')
@require_login
def progress_stream(session_id):
    def generate():
        q = processing_queues.get(session_id)
        if not q:
            yield f'data: {json.dumps({"error": "No active job"})}\n\n'
            return
        while True:
            try:
                msg = q.get(timeout=30)
                yield f'data: {json.dumps(msg)}\n\n'
                if msg.get('done') or msg.get('error'):
                    break
            except queue.Empty:
                yield 'data: {"ping":true}\n\n'

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )

def _process_session(session_id, cfg, progress_q, user_email='unknown'):
    from processor.claude_vision import process_item

    session = load_session(session_id)
    groups = session.get('groups', [])
    folder_path = session['folder_path']
    total = len(groups)

    existing = {item['id']: item for item in load_items(session_id)}
    results = [None] * total

    def process_one(idx, group):
        item_id_base = f'item_{idx:04d}'

        if item_id_base in existing and existing[item_id_base].get('status') == 'processed':
            return [existing[item_id_base]]

        multi = sorted(
            [v for k, v in existing.items()
             if k.startswith(item_id_base + '_') and v.get('status') == 'processed'],
            key=lambda x: x['id']
        )
        if multi:
            return multi

        photo_paths = [str(Path(folder_path) / p) for p in group]
        try:
            detected = process_item(photo_paths, cfg['anthropic_api_key'], cfg.get('model'))
            items_out = []
            for i, d in enumerate(detected):
                item_id = item_id_base if len(detected) == 1 else f'{item_id_base}_{i}'
                items_out.append({
                    'id': item_id,
                    'photos': group,
                    'primary_photo': group[0],
                    'category': d.get('category', ''),
                    'manufacturer': d.get('manufacturer', ''),
                    'item': d.get('item', ''),
                    'model_serial': d.get('model_serial', ''),
                    'quantity': d.get('quantity', 1),
                    'price': None,
                    'age': None,
                    'confidence': d.get('confidence', {'overall': 0.5, 'flags': []}),
                    'price_suggestions': [],
                    'status': 'processed',
                })
            return items_out
        except Exception as e:
            return [{
                'id': item_id_base,
                'photos': group,
                'primary_photo': group[0],
                'category': '', 'manufacturer': '', 'item': '',
                'model_serial': '', 'quantity': 1, 'price': None, 'age': None,
                'confidence': {'overall': 0, 'flags': [str(e)]},
                'price_suggestions': [],
                'status': 'error',
            }]

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(process_one, idx, grp): idx for idx, grp in enumerate(groups)}
        for future in as_completed(futures):
            idx = futures[future]
            try:
                items = future.result()
                results[idx] = items
                completed = sum(1 for r in results if r is not None)
                first = items[0] if items else {}
                progress_q.put({
                    'completed': completed,
                    'total': total,
                    'item_label': first.get('item') or groups[idx][0],
                    'photo': groups[idx][0],
                    'new_items': items,
                })
                flat = [item for r in results if r is not None for item in r]
                save_items(session_id, flat)
            except Exception as e:
                progress_q.put({'error': str(e)})

    final = [item for r in results if r is not None for item in r]
    save_items(session_id, final)
    session['status'] = 'reviewing'
    session['updated_at'] = datetime.now().isoformat()
    save_session(session_id, session)
    log_usage(user_email, 'processing_completed',
              session_id=session_id, item_count=len(final), model=cfg.get('model', ''))
    progress_q.put({'done': True, 'total': total})

# ── Items ─────────────────────────────────────────────────────────────────────

@app.route('/api/sessions/<session_id>/items/<item_id>', methods=['PUT'])
@require_login
def update_item(session_id, item_id):
    items = load_items(session_id)
    data = request.json or {}
    for item in items:
        if item['id'] == item_id:
            for field in ('category', 'manufacturer', 'item', 'model_serial',
                          'quantity', 'price', 'age', 'price_source_url'):
                if field in data:
                    item[field] = data[field]
            item['status'] = 'reviewed'
            break
    save_items(session_id, items)
    return jsonify({'ok': True})

@app.route('/api/sessions/<session_id>/items/<item_id>', methods=['DELETE'])
@require_login
def delete_item(session_id, item_id):
    items = load_items(session_id)
    items = [i for i in items if i['id'] != item_id]
    save_items(session_id, items)
    return jsonify({'ok': True})

@app.route('/api/sessions/<session_id>/items/<item_id>/split', methods=['POST'])
@require_login
def split_item(session_id, item_id):
    items = load_items(session_id)
    idx = next((i for i, item in enumerate(items) if item['id'] == item_id), None)
    if idx is None:
        return jsonify({'error': 'Item not found'}), 404
    original = items[idx]
    new_id = item_id + '_s' + str(int(time.time()))[-5:]
    copy = {
        **original,
        'id': new_id,
        'item': '',
        'manufacturer': '',
        'model_serial': '',
        'quantity': 1,
        'price': None,
        'age': None,
        'price_suggestions': [],
        'status': 'reviewed',
        'confidence': {'overall': 0.5, 'flags': ['Manually split \u2014 fill in details']},
        'flagged': False,
    }
    items.insert(idx + 1, copy)
    save_items(session_id, items)
    return jsonify({'item': copy})

@app.route('/api/sessions/<session_id>/items/merge', methods=['POST'])
@require_login
def merge_items(session_id):
    data = request.json or {}
    keep_id   = data.get('keep_id')
    remove_id = data.get('remove_id')
    items = load_items(session_id)
    keep   = next((i for i in items if i['id'] == keep_id), None)
    remove = next((i for i in items if i['id'] == remove_id), None)
    if not keep or not remove:
        return jsonify({'error': 'Item not found'}), 404
    extra_photos = [p for p in (remove.get('photos') or []) if p not in (keep.get('photos') or [])]
    keep['photos'] = (keep.get('photos') or []) + extra_photos
    items = [i for i in items if i['id'] != remove_id]
    save_items(session_id, items)
    return jsonify({'ok': True, 'item': keep})

@app.route('/api/sessions/<session_id>/items/<item_id>/flag', methods=['POST'])
@require_login
def flag_item(session_id, item_id):
    data = request.json or {}
    items = load_items(session_id)
    item = next((i for i in items if i['id'] == item_id), None)
    if not item:
        return jsonify({'error': 'Item not found'}), 404

    entry = {
        'timestamp': datetime.now().isoformat(),
        'user': g.current_user.get('email', 'unknown'),
        'session_id': session_id,
        'item_id': item_id,
        'item_data': {k: v for k, v in item.items() if k != 'price_suggestions'},
        'note': data.get('note', ''),
    }
    with _feedback_lock:
        with open(FEEDBACK_DIR / 'feedback_log.jsonl', 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry) + '\n')

    item['flagged'] = True
    save_items(session_id, items)
    return jsonify({'ok': True})

# ── Price search ──────────────────────────────────────────────────────────────

@app.route('/api/sessions/<session_id>/items/<item_id>/price-search', methods=['POST'])
@require_login
def price_search(session_id, item_id):
    cfg = load_config()
    if not cfg.get('anthropic_api_key'):
        return jsonify({'error': 'Anthropic API key not configured'}), 400
    items = load_items(session_id)
    item = next((i for i in items if i['id'] == item_id), None)
    if not item:
        return jsonify({'error': 'Item not found'}), 404

    data = request.json or {}
    messages = data.get('messages') or None

    from processor.price_search import search_price
    result = search_price(
        item.get('manufacturer', ''),
        item.get('item', ''),
        item.get('model_serial', ''),
        cfg['anthropic_api_key'],
        cfg.get('model'),
        messages=messages,
    )

    if 'error' in result:
        return jsonify({'error': result['error']}), 502

    if 'question' in result:
        return jsonify({'question': result['question'], 'messages': result['messages']})

    suggestions = result.get('suggestions', [])
    item['price_suggestions'] = suggestions
    save_items(session_id, items)
    return jsonify({'suggestions': suggestions})

@app.route('/api/sessions/<session_id>/price-search-all', methods=['POST'])
@require_login
def price_search_all(session_id):
    cfg = load_config()
    if not cfg.get('anthropic_api_key'):
        return jsonify({'error': 'Anthropic API key not configured'}), 400

    from processor.price_search import search_price
    items = load_items(session_id)
    updated = 0
    last_error = None
    for item in items:
        if not item.get('price_suggestions'):
            result = search_price(
                item.get('manufacturer', ''),
                item.get('item', ''),
                item.get('model_serial', ''),
                cfg['anthropic_api_key'],
                cfg.get('model'),
            )
            if 'error' in result:
                last_error = result['error']
                break
            suggestions = result.get('suggestions', [])
            if suggestions:
                item['price_suggestions'] = suggestions
                updated += 1
    save_items(session_id, items)
    if last_error and updated == 0:
        return jsonify({'error': last_error}), 502
    return jsonify({'updated': updated})

# ── Export ────────────────────────────────────────────────────────────────────

@app.route('/api/sessions/<session_id>/export', methods=['POST'])
@require_login
def export_session(session_id):
    data = request.json or {}
    output_path = data.get('output_path')
    session = load_session(session_id)
    if not session:
        return jsonify({'error': 'Session not found'}), 404

    if not output_path:
        folder = Path(session['folder_path'])
        ts = datetime.now().strftime('%Y%m%d-%H%M%S')
        output_path = str(folder / f'Inventory-{ts}.xlsx')

    items = load_items(session_id)
    from processor.export import export_to_excel
    export_to_excel(items, output_path, session['folder_path'])

    session['status'] = 'exported'
    session['updated_at'] = datetime.now().isoformat()
    save_session(session_id, session)
    log_usage(g.current_user.get('email', 'unknown'), 'export',
              session_id=session_id, item_count=len(items))

    return jsonify({'path': output_path})

# ── Main ──────────────────────────────────────────────────────────────────────

@app.route('/')
@require_login
def index():
    return render_template('index.html', user=flask_session.get('user', {}))

def _open_browser():
    time.sleep(1.5)
    webbrowser.open('http://localhost:5000')

def _is_port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('localhost', port)) == 0

if __name__ == '__main__':
    if _is_port_in_use(5000):
        webbrowser.open('http://localhost:5000')
    else:
        threading.Thread(target=_open_browser, daemon=True).start()
        print('\n  Liberty Restoration - Inventory Bot')
        print('  Running at http://localhost:5000')
        print('  Close this window to stop the server.\n')
        app.run(debug=False, port=5000, threaded=True)
