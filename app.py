import os
import uuid
import re
import json as json_lib
import hashlib
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


# ── DB接続（占いスタジオと同じ） ───────────────────────────────
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
        conn.commit()
        cur.close()
        conn.close()
        print('coconala_research table ready')
    except Exception as e:
        print(f'init_db error: {e}')


# ── 一時ファイル削除（占いスタジオと同じ） ────────────────────
def delete_later(path, delay=1800):
    def _delete():
        time.sleep(delay)
        try:
            os.remove(path)
        except:
            pass
    threading.Thread(target=_delete, daemon=True).start()


# ── 一時ファイル配信（占いスタジオと同じ） ────────────────────
@app.route('/files/<filename>', methods=['GET'])
def serve_file(filename):
    return send_from_directory(UPLOAD_DIR, filename)


# ── Step1: ファイル受取 → /tmp保存 → Pabbly → Drive URL取得 ──
# （占いスタジオの /upload と同じ実装）
@app.route('/research/upload', methods=['POST'])
def research_upload():
    if 'file' not in request.files:
        return jsonify({'error': 'fileが必要です'}), 400

    file = request.files['file']
    ext  = os.path.splitext(file.filename)[1].lower()
    if ext not in ['.jpg', '.jpeg', '.png', '.gif', '.webp']:
        ext = '.jpg'  # 拡張子不明・非対応の場合はjpgとして扱う

    filename = str(uuid.uuid4()) + ext
    filepath = os.path.join(UPLOAD_DIR, filename)
    file.save(filepath)

    # 一時公開URL
    base     = BASE_URL.rstrip('/')
    temp_url = f'{base}/files/{filename}'

    # Pabbly API → Drive保存
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
                'folderId':  DRIVE_FOLDER_ID,
                'file_name': filename,
            },
            timeout=30,
        )
        data = res.json()
        print(f'Pabbly Drive upload response: {data}')

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
        print(f'Pabbly Drive error: {e}')

    delete_later(filepath, 1800)

    return jsonify({
        'temp_url':  temp_url,
        'drive_url': drive_url,
        'filename':  filename,
        'error':     error_msg,
    })


# ── Step2: Drive URL → Cloudinary URL（Pabbly経由） ──────────
# （占いスタジオの /cloudinary/upload と同じ実装）
@app.route('/research/cloudinary', methods=['POST'])
def research_cloudinary():
    data      = request.json or {}
    drive_url = data.get('drive_url', '')

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
                'public_id':     '',
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


# ── Step3: Cloudinary URL → Pabbly Vision → response_id ─────
# （占いスタジオの /accounts/analyze と同じ実装）
COCONALA_VISION_PROMPT = '{"service_title":"サービスのタイトル（ページ最上部の大きな見出し）","seller_name":"出品者名・ハンドルネーム","seller_profile":"出品者のプロフィール文・自己紹介文","caption":"サービスの説明文・キャプション（できるだけ全文）","price":"価格（数字のみ、最安値、円マーク不要）","reviews":"評価件数（数字のみ）","category":"タロット / 占星術 / 数秘術 / 手相 / 霊視 / その他 のどれか1つ"} このスクリーンショットはコナラのサービスページです。上記JSON形式のみで返してください。JSONのみ、コードブロック不要。'

@app.route('/research/analyze', methods=['POST'])
def research_analyze():
    data      = request.json or {}
    image_url = data.get('image_url', '')
    if not image_url:
        return jsonify({'error': 'image_urlが必要です'}), 400
    if not PABBLY_VISION_URL:
        return jsonify({'error': 'PABBLY_VISION_URLが未設定'}), 500

    try:
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
        d = res.json()
        response_id = d.get('id') or (d.get('data') or {}).get('id') or ''
        if not response_id:
            return jsonify({'error': 'response_id取得失敗', 'raw': str(d)[:200]}), 500
        return jsonify({'response_id': response_id})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── Step4: ポーリングで結果取得 ──────────────────────────────
# （占いスタジオの /accounts/result と同じ実装）
@app.route('/research/result', methods=['POST'])
def research_result():
    data        = request.json or {}
    response_id = data.get('response_id', '')
    if not response_id:
        return jsonify({'error': 'response_idが必要'}), 400

    try:
        res = requests.post(
            PABBLY_RESPONSE_URL,
            headers={
                'Content-Type':  'application/json',
                'Authorization': f'Bearer {PABBLY_AUTH}'
            },
            json={'ResponseID': response_id},
            timeout=30
        )
        raw       = res.text
        clean_raw = re.sub(r'<=-\+\(\$@\$\)\+-=>', '_', raw)
        try:
            result = json_lib.loads(clean_raw)
        except:
            result = {}

        actual = result.get('data') or result
        if isinstance(actual, str):
            try:
                actual = json_lib.loads(re.sub(r'<=-\+\(\$@\$\)\+-=>', '_', actual))
            except:
                pass
        if not actual.get('output') and result.get('output'):
            actual = result

        status = actual.get('status') or result.get('status') or ''
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


# ── 起動 ────────────────────────────────────────────────────
init_db()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
