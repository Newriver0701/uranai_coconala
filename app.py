import os
import uuid
import re
import json as json_lib
import time
import threading
import requests
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

app = Flask(__name__, static_folder='static')
CORS(app)

UPLOAD_DIR = '/tmp/uploads'
os.makedirs(UPLOAD_DIR, exist_ok=True)

# ── 環境変数（占いスタジオと完全共通） ────────────────────────
DATABASE_URL          = os.environ.get('DATABASE_URL', '')
PABBLY_API_URL        = os.environ.get('PABBLY_API_URL', '')
PABBLY_CLOUDINARY_URL = os.environ.get('PABBLY_CLOUDINARY_URL', '')
PABBLY_VISION_URL     = os.environ.get('PABBLY_VISION_URL', '')
PABBLY_RESPONSE_URL   = os.environ.get('PABBLY_RESPONSE_URL', '')
PABBLY_AUTH           = os.environ.get('PABBLY_AUTH', '')
BASE_URL              = os.environ.get('BASE_URL', '')
DRIVE_FOLDER_ID       = os.environ.get('DRIVE_FOLDER_ID', '')
PABBLY_LIST_API_URL   = os.environ.get('PABBLY_LIST_API_URL', '')


# ── DB ───────────────────────────────────────────────────────
def get_db():
    import psycopg2
    return psycopg2.connect(DATABASE_URL)

def init_db():
    if not DATABASE_URL:
        return
    try:
        conn = get_db()
        cur  = conn.cursor()
        cur.execute('''
            CREATE TABLE IF NOT EXISTS coconala_research (
                id             SERIAL PRIMARY KEY,
                seller_name    VARCHAR(200),
                seller_profile TEXT,
                service_title  VARCHAR(400) NOT NULL,
                caption        TEXT,
                price          VARCHAR(50),
                reviews        VARCHAR(50),
                category       VARCHAR(100),
                image_url      TEXT,
                notes          TEXT,
                created_at     TIMESTAMP DEFAULT NOW(),
                updated_at     TIMESTAMP DEFAULT NOW()
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS research_library (
                id             SERIAL PRIMARY KEY,
                drive_file_id  TEXT UNIQUE,
                cloudinary_url TEXT DEFAULT \'\',
                hidden         BOOLEAN DEFAULT FALSE,
                created_at     TIMESTAMP DEFAULT NOW()
            )
        ''')
        # hiddenカラムがなければ追加
        try:
            cur.execute("ALTER TABLE research_library ADD COLUMN IF NOT EXISTS hidden BOOLEAN DEFAULT FALSE")
        except:
            pass
        conn.commit()
        cur.close()
        conn.close()
        print('coconala_research table ready')
    except Exception as e:
        print(f'init_db error: {e}')


# ── 一時ファイル削除・配信（占いスタジオと同じ） ──────────────
def delete_later(path, delay=1800):
    def _delete():
        time.sleep(delay)
        try:
            os.remove(path)
        except:
            pass
    threading.Thread(target=_delete, daemon=True).start()

@app.route('/files/<filename>', methods=['GET'])
def serve_file(filename):
    return send_from_directory(UPLOAD_DIR, filename)



# ── /drive/list: 占いスタジオと完全同一 ──────────────────────
@app.route('/drive/list', methods=['POST'])
def drive_list():
    data      = request.json or {}
    folder_id = data.get('folderId', '') or data.get('folder_id', '') or DRIVE_FOLDER_ID

    if not folder_id:
        return jsonify({'error': 'folderIdが必要です'}), 400
    if not PABBLY_LIST_API_URL or not PABBLY_AUTH:
        return jsonify({'error': 'PABBLY_LIST_API_URL が未設定'}), 500

    try:
        res = requests.post(
            PABBLY_LIST_API_URL,
            headers={
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {PABBLY_AUTH}',
            },
            json={'parent_id': folder_id},
            timeout=60,
        )
        raw    = res.text
        parsed = json_lib.loads(raw)
        result = []

        if isinstance(parsed, dict) and 'data' in parsed:
            data_val = parsed['data']
            if isinstance(data_val, dict):
                if 'raw_data' in data_val:
                    raw_parsed = json_lib.loads(data_val['raw_data'])
                    if isinstance(raw_parsed, dict) and 'files' in raw_parsed:
                        files_val = raw_parsed['files']
                        result = json_lib.loads(files_val) if isinstance(files_val, str) else files_val
                    elif isinstance(raw_parsed, list):
                        result = raw_parsed
                elif 'files' in data_val:
                    files_val = data_val['files']
                    result = json_lib.loads(files_val) if isinstance(files_val, str) else files_val
            elif isinstance(data_val, list):
                result = data_val
        elif isinstance(parsed, dict) and 'files' in parsed:
            files_val = parsed['files']
            result = json_lib.loads(files_val) if isinstance(files_val, str) else files_val
        elif isinstance(parsed, list):
            result = parsed

        for f in result:
            fid = f.get('id', '')
            if fid:
                f['thumbnailLink'] = f'https://drive.google.com/thumbnail?id={fid}&sz=w400'

        return app.response_class(
            response=json_lib.dumps(result, ensure_ascii=False),
            status=200, mimetype='application/json'
        )
    except Exception as e:
        print(f'Drive list error: {e}')
        return jsonify({'error': str(e)}), 500


# ── /proxy/image: Driveサムネをプロキシ配信 ──────────────────
@app.route('/proxy/image', methods=['GET'])
def proxy_image():
    from flask import Response
    file_id = request.args.get('id', '')
    if not file_id: return 'id missing', 400
    try:
        thumb_url = f'https://drive.google.com/thumbnail?id={file_id}&sz=w400'
        r = requests.get(thumb_url, timeout=15, allow_redirects=True,
            headers={'User-Agent': 'Mozilla/5.0'})
        if r.status_code == 200 and len(r.content) > 500:
            ct = r.headers.get('Content-Type', 'image/jpeg').split(';')[0]
            resp = Response(r.content, status=200, mimetype=ct)
            resp.headers['Cache-Control'] = 'public, max-age=3600'
            return resp
        dl_url = f'https://drive.google.com/uc?export=download&id={file_id}'
        r2 = requests.get(dl_url, timeout=15, allow_redirects=True,
            headers={'User-Agent': 'Mozilla/5.0'})
        ct2 = r2.headers.get('Content-Type', 'image/jpeg').split(';')[0]
        resp2 = Response(r2.content, status=200, mimetype=ct2)
        resp2.headers['Cache-Control'] = 'public, max-age=3600'
        return resp2
    except Exception as e:
        return str(e), 500


# ── /upload: ファイル → /tmp → Pabbly → Drive URL ───────────
@app.route('/upload', methods=['POST'])
def upload():
    if 'file' not in request.files:
        return jsonify({'error': 'ファイルがありません'}), 400

    file       = request.files['file']
    folder_id  = request.form.get('folderId', DRIVE_FOLDER_ID)
    file_label = request.form.get('file_name', '')

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ['.jpg', '.jpeg', '.png', '.gif', '.webp']:
        ext = '.jpg'

    filename = str(uuid.uuid4()) + ext
    filepath = os.path.join(UPLOAD_DIR, filename)
    file.save(filepath)

    if not file_label:
        file_label = file.filename or filename

    base     = BASE_URL.rstrip('/')
    temp_url = f'{base}/files/{filename}'

    drive_url = ''
    error_msg = ''
    try:
        if not PABBLY_API_URL or not PABBLY_AUTH:
            raise ValueError('PABBLY_API_URL または PABBLY_AUTH が未設定')

        res = requests.post(
            PABBLY_API_URL,
            headers={
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {PABBLY_AUTH}',
            },
            json={
                'url':       temp_url,
                'folderId':  folder_id,
                'file_name': file_label,
            },
            timeout=30,
        )
        data = res.json()
        print(f'Pabbly upload response: {data}')

        file_id = (
            data.get('response', {}).get('result', {}).get('uploadedFileId') or
            data.get('id') or
            data.get('fileId') or
            data.get('data', {}).get('id') or
            ''
        )
        if file_id:
            drive_url = f'https://drive.google.com/uc?export=view&id={file_id}'
        else:
            drive_url = (
                data.get('webViewLink') or
                data.get('url') or
                data.get('file_url') or
                ''
            )
    except Exception as e:
        error_msg = str(e)
        print(f'Pabbly error: {e}')

    delete_later(filepath, 1800)

    return jsonify({
        'temp_url':  temp_url,
        'drive_url': drive_url,
        'filename':  filename,
        'error':     error_msg,
    })



# ── /library/cloudinary/save: drive_id→cloudinary_url をDB保存 ──
@app.route('/library/cloudinary/save', methods=['POST'])
def library_cloudinary_save():
    data           = request.json or {}
    drive_file_id  = data.get('drive_file_id', '')
    cloudinary_url = data.get('cloudinary_url', '')
    if not drive_file_id or not cloudinary_url:
        return jsonify({'error': '必須項目不足'}), 400
    if not DATABASE_URL:
        return jsonify({'error': 'DB未設定'}), 500
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute('''INSERT INTO research_library (drive_file_id, cloudinary_url)
            VALUES (%s, %s)
            ON CONFLICT (drive_file_id) DO UPDATE SET cloudinary_url = EXCLUDED.cloudinary_url''',
            (drive_file_id, cloudinary_url))
        conn.commit(); cur.close(); conn.close()
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── /library/cloudinary/list: drive_idリスト→cloudinary_urlマップ取得 ──
@app.route('/library/cloudinary/list', methods=['POST'])
def library_cloudinary_list():
    data     = request.json or {}
    file_ids = data.get('file_ids', [])
    if not file_ids or not DATABASE_URL:
        return jsonify({})
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute(
            'SELECT drive_file_id, cloudinary_url FROM research_library WHERE drive_file_id = ANY(%s)',
            (file_ids,))
        rows = cur.fetchall(); cur.close(); conn.close()
        return jsonify({r[0]: r[1] for r in rows if r[1]})
    except Exception as e:
        return jsonify({'error': str(e)}), 500



# ── /library/hide: drive_file_idを非表示に ──────────────────
@app.route('/library/hide', methods=['POST'])
def library_hide():
    data          = request.json or {}
    drive_file_id = data.get('drive_file_id', '')
    if not drive_file_id:
        return jsonify({'error': 'drive_file_idが必要'}), 400
    if not DATABASE_URL:
        return jsonify({'error': 'DB未設定'}), 500
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute('''INSERT INTO research_library (drive_file_id, hidden)
            VALUES (%s, TRUE)
            ON CONFLICT (drive_file_id) DO UPDATE SET hidden = TRUE''',
            (drive_file_id,))
        conn.commit(); cur.close(); conn.close()
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── /library/hidden/list: 非表示IDリスト取得 ─────────────────
@app.route('/library/hidden/list', methods=['POST'])
def library_hidden_list():
    if not DATABASE_URL:
        return jsonify([])
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute('SELECT drive_file_id FROM research_library WHERE hidden = TRUE')
        rows = cur.fetchall(); cur.close(); conn.close()
        return jsonify([r[0] for r in rows])
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── /cloudinary/upload: 占いスタジオと完全同一 ───────────────
@app.route('/cloudinary/upload', methods=['POST'])
def cloudinary_upload():
    data      = request.json or {}
    drive_url = data.get('drive_url', '')
    public_id = data.get('public_id', '')

    if not PABBLY_CLOUDINARY_URL:
        return jsonify({'error': 'PABBLY_CLOUDINARY_URL が未設定'}), 500
    if not drive_url:
        return jsonify({'error': 'drive_urlが必要です'}), 400

    try:
        res = requests.post(
            PABBLY_CLOUDINARY_URL,
            headers={
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {PABBLY_AUTH}',
            },
            json={
                'file':          drive_url,
                'resource_type': 'image',
                'upload_preset': 'ml_default',
                'public_id':     public_id,
                'tags':          ''
            },
            timeout=60,
        )
        raw    = res.text
        parsed = json_lib.loads(raw)
        print(f'Cloudinary raw: {raw[:300]}')

        def extract_url(obj):
            if isinstance(obj, dict):
                for key in ['secure_url', 'url']:
                    if obj.get(key):
                        return obj[key]
                inner = obj.get('data', {})
                if isinstance(inner, str):
                    try:
                        inner = json_lib.loads(inner)
                    except:
                        pass
                if isinstance(inner, dict):
                    for key in ['secure_url', 'url']:
                        if inner.get(key):
                            return inner[key]
            return ''

        url = extract_url(parsed)
        if not url:
            return jsonify({'error': 'CloudinaryのURL取得失敗', 'raw': raw[:300]}), 500
        return jsonify({'url': url})

    except Exception as e:
        print(f'Cloudinary error: {e}')
        return jsonify({'error': str(e)}), 500


# ── /analyze: Vision AI（Pabbly経由） ────────────────────────
COCONALA_VISION_PROMPT = "このスクリーンショットはコナラのサービスページです。以下のJSON形式のみで返してください。コードブロック不要。{\"service_title\":\"サービスタイトル\",\"seller_name\":\"出品者名\",\"seller_profile\":\"プロフィール文\",\"caption\":\"サービス説明文\",\"price\":\"価格(数字のみ)\",\"reviews\":\"評価件数(数字のみ)\",\"category\":\"タロット/占星術/数秘術/手相/霊視/その他のどれか\"}"

@app.route('/analyze', methods=['POST'])
def analyze():
    data      = request.json or {}
    image_url = data.get('image_url', '')
    if not image_url:
        return jsonify({'error': 'image_urlが必要です'}), 400
    if not PABBLY_VISION_URL:
        return jsonify({'error': 'PABBLY_VISION_URLが未設定'}), 500

    try:
        print(f'[analyze] URL={PABBLY_VISION_URL[:60]}')
        print(f'[analyze] AUTH={PABBLY_AUTH[:20] if PABBLY_AUTH else "EMPTY"}')
        print(f'[analyze] image_url={image_url[:80]}')
        res = requests.post(
            PABBLY_VISION_URL,
            headers={
                'Content-Type':  'application/json',
                'Authorization': f'Bearer {PABBLY_AUTH}'
            },
            json={
                'model':      'gpt-4o-mini',
                'role':       '',
                'image_url':  image_url,
                'text':       COCONALA_VISION_PROMPT,
                'background': '1'
            },
            timeout=30
        )
        print(f'[analyze] status={res.status_code} raw={res.text[:300]}')
        d = res.json()

        # パターン1: {"id": "resp_..."}
        response_id = d.get('id') or ''

        # パターン2: {"data": {"id": "resp_..."}}
        if not response_id:
            response_id = (d.get('data') or {}).get('id') or ''

        # パターン3: {"data": {"raw_data": "{"id":"resp_..."}"}}
        if not response_id:
            raw_data = (d.get('data') or {}).get('raw_data', '')
            if raw_data:
                try:
                    rd = json_lib.loads(raw_data)
                    response_id = rd.get('id') or ''
                except:
                    pass

        print(f'[analyze] response_id={response_id}')
        if not response_id:
            return jsonify({'error': 'response_id取得失敗', 'raw': str(d)[:300]}), 500
        return jsonify({'response_id': response_id})
    except Exception as e:
        print(f'[analyze] exception={e}')
        return jsonify({'error': str(e)}), 500


# ── /analyze/result: Vision結果ポーリング ────────────────────
@app.route('/analyze/result', methods=['POST'])
def analyze_result():
    data        = request.json or {}
    response_id = data.get('response_id', '')
    if not response_id:
        return jsonify({'error': 'response_idが必要'}), 400

    try:
        print(f'[analyze/result] response_id={response_id}')
        print(f'[analyze/result] PABBLY_RESPONSE_URL={PABBLY_RESPONSE_URL[:60]}')
        res = requests.post(
            PABBLY_RESPONSE_URL,
            headers={
                'Content-Type':  'application/json',
                'Authorization': f'Bearer {PABBLY_AUTH}'
            },
            json={'ResponseID': response_id},
            timeout=30
        )
        print(f'[analyze/result] status={res.status_code} raw={res.text[:400]}')
        raw       = res.text
        clean_raw = re.sub(r'<=-\+\(\$@\$\)\+-=>', '_', raw)
        try:
            result = json_lib.loads(clean_raw)
        except:
            result = {}

        # Pabblyのレスポンス構造: {"data": {"raw_data": "{...actual JSON...}"}}
        actual = {}
        data_val = result.get('data', {})
        if isinstance(data_val, dict) and 'raw_data' in data_val:
            try:
                actual = json_lib.loads(data_val['raw_data'])
                print(f'[analyze/result] actual_status={actual.get("status")}')
            except:
                actual = data_val
        elif isinstance(data_val, str):
            try:
                actual = json_lib.loads(data_val)
            except:
                actual = {}
        else:
            actual = data_val or result

        if not actual:
            actual = result

        status = actual.get('status') or result.get('status') or ''
        print(f'[analyze/result] status={status}')
        if status in ('queued', 'in_progress'):
            return jsonify({'status': 'processing'})

        text_content = ''
        try:
            output_raw = actual.get('output') or result.get('output') or []
            if isinstance(output_raw, str):
                try:
                    output_raw = json_lib.loads(output_raw)
                except:
                    output_raw = []
            if output_raw and isinstance(output_raw, list):
                content_list = output_raw[0].get('content', [])
                if isinstance(content_list, str):
                    try:
                        content_list = json_lib.loads(content_list)
                    except:
                        content_list = []
                if isinstance(content_list, list):
                    for c in content_list:
                        if isinstance(c, dict) and c.get('type') == 'output_text':
                            text_content = c.get('text', '')
                            break
        except:
            pass

        print(f'[analyze/result] text_content={text_content[:200]}')
        parsed = {}
        try:
            clean = re.sub(r'```json|```', '', text_content).strip()
            parsed = json_lib.loads(clean)
        except:
            return jsonify({'status': 'error', 'error': 'JSONパース失敗', 'raw': text_content[:300]})

        return jsonify({'status': 'done', 'data': parsed})

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── CRUD ─────────────────────────────────────────────────────
@app.route('/research/entries', methods=['GET'])
def entries_list():
    if not DATABASE_URL:
        return jsonify([])
    search   = request.args.get('search', '')
    category = request.args.get('category', '')
    try:
        conn = get_db()
        cur  = conn.cursor()
        if search and category and category != 'すべて':
            cur.execute('''
                SELECT id,seller_name,seller_profile,service_title,caption,
                       price,reviews,category,image_url,notes,created_at
                FROM coconala_research
                WHERE (service_title ILIKE %s OR seller_name ILIKE %s OR caption ILIKE %s)
                  AND category=%s ORDER BY created_at DESC
            ''', (f'%{search}%', f'%{search}%', f'%{search}%', category))
        elif search:
            cur.execute('''
                SELECT id,seller_name,seller_profile,service_title,caption,
                       price,reviews,category,image_url,notes,created_at
                FROM coconala_research
                WHERE service_title ILIKE %s OR seller_name ILIKE %s OR caption ILIKE %s
                ORDER BY created_at DESC
            ''', (f'%{search}%', f'%{search}%', f'%{search}%'))
        elif category and category != 'すべて':
            cur.execute('''
                SELECT id,seller_name,seller_profile,service_title,caption,
                       price,reviews,category,image_url,notes,created_at
                FROM coconala_research WHERE category=%s ORDER BY created_at DESC
            ''', (category,))
        else:
            cur.execute('''
                SELECT id,seller_name,seller_profile,service_title,caption,
                       price,reviews,category,image_url,notes,created_at
                FROM coconala_research ORDER BY created_at DESC
            ''')
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return jsonify([{
            'id': r[0], 'seller_name': r[1] or '', 'seller_profile': r[2] or '',
            'service_title': r[3], 'caption': r[4] or '', 'price': r[5] or '',
            'reviews': r[6] or '', 'category': r[7] or '', 'image_url': r[8] or '',
            'notes': r[9] or '',
            'created_at': r[10].strftime('%Y/%m/%d %H:%M') if r[10] else ''
        } for r in rows])
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/research/entries', methods=['POST'])
def entries_create():
    if not DATABASE_URL:
        return jsonify({'error': 'DB未設定'}), 500
    data = request.json or {}
    if not data.get('service_title', '').strip():
        return jsonify({'error': 'service_titleが必要'}), 400
    try:
        conn = get_db()
        cur  = conn.cursor()
        cur.execute('''
            INSERT INTO coconala_research
              (seller_name,seller_profile,service_title,caption,price,reviews,category,image_url,notes)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id,created_at
        ''', (
            data.get('seller_name',''), data.get('seller_profile',''),
            data.get('service_title',''), data.get('caption',''),
            data.get('price',''), data.get('reviews',''),
            data.get('category',''), data.get('image_url',''), data.get('notes','')
        ))
        row = cur.fetchone()
        conn.commit(); cur.close(); conn.close()
        return jsonify({'status':'ok','id':row[0],'created_at':row[1].strftime('%Y/%m/%d %H:%M')}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/research/entries/<int:entry_id>', methods=['GET'])
def entries_get(entry_id):
    if not DATABASE_URL:
        return jsonify({'error': 'DB未設定'}), 500
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute('''
            SELECT id,seller_name,seller_profile,service_title,caption,
                   price,reviews,category,image_url,notes,created_at
            FROM coconala_research WHERE id=%s
        ''', (entry_id,))
        r = cur.fetchone(); cur.close(); conn.close()
        if not r: return jsonify({'error':'not found'}), 404
        return jsonify({
            'id':r[0],'seller_name':r[1] or '','seller_profile':r[2] or '',
            'service_title':r[3],'caption':r[4] or '','price':r[5] or '',
            'reviews':r[6] or '','category':r[7] or '','image_url':r[8] or '',
            'notes':r[9] or '','created_at':r[10].strftime('%Y/%m/%d %H:%M') if r[10] else ''
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/research/entries/<int:entry_id>', methods=['PUT'])
def entries_update(entry_id):
    if not DATABASE_URL:
        return jsonify({'error': 'DB未設定'}), 500
    data = request.json or {}
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute('''
            UPDATE coconala_research SET
              seller_name=%s,seller_profile=%s,service_title=%s,caption=%s,
              price=%s,reviews=%s,category=%s,image_url=%s,notes=%s,updated_at=NOW()
            WHERE id=%s
        ''', (
            data.get('seller_name',''), data.get('seller_profile',''),
            data.get('service_title',''), data.get('caption',''),
            data.get('price',''), data.get('reviews',''),
            data.get('category',''), data.get('image_url',''),
            data.get('notes',''), entry_id
        ))
        conn.commit(); cur.close(); conn.close()
        return jsonify({'status':'ok'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/research/entries/<int:entry_id>', methods=['DELETE'])
def entries_delete(entry_id):
    if not DATABASE_URL:
        return jsonify({'error': 'DB未設定'}), 500
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute('DELETE FROM coconala_research WHERE id=%s', (entry_id,))
        conn.commit(); cur.close(); conn.close()
        return jsonify({'status':'ok','deleted':entry_id})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── 静的ファイル配信 ────────────────────────────────────────
@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'})

@app.route('/', methods=['GET'])
def index():
    return send_from_directory('static', 'index.html')

@app.route('/<path:filename>', methods=['GET'])
def static_files(filename):
    return send_from_directory('static', filename)


init_db()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
