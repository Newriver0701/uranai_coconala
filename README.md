# RESEARCH VAULT — ココナラ競合リサーチDB

## 構成
- Flask + PostgreSQL（占いスタジオと同DBを共有）
- Cloudinary（画像アップロード）
- Railway（デプロイ先）

## Railway デプロイ手順

### 1. GitHubにプッシュ
```bash
git init
git add .
git commit -m "init"
git remote add origin <your-repo-url>
git push -u origin main
```

### 2. Railwayで新規プロジェクト作成
- railway.app → New Project → Deploy from GitHub repo
- このリポジトリを選択

### 3. 環境変数を設定（Settings → Variables）

| 変数名 | 値 |
|--------|-----|
| `DATABASE_URL` | 占いスタジオのDBと同じ接続文字列 |
| `CLOUDINARY_CLOUD_NAME` | Cloudinaryのcloud name |
| `CLOUDINARY_API_KEY` | CloudinaryのAPI Key |
| `CLOUDINARY_API_SECRET` | CloudinaryのAPI Secret |

### 4. デプロイ確認
- デプロイ後、自動的に `coconala_research` テーブルが作成される
- 占いスタジオのDBに同居するだけでテーブルは独立しているので干渉なし

## DBテーブル構造
```sql
CREATE TABLE coconala_research (
  id              SERIAL PRIMARY KEY,
  seller_name     VARCHAR(200),
  seller_profile  TEXT,
  service_title   VARCHAR(400) NOT NULL,
  caption         TEXT,
  price           VARCHAR(50),
  reviews         VARCHAR(50),
  category        VARCHAR(100),
  image_url       TEXT,
  notes           TEXT,
  created_at      TIMESTAMP DEFAULT NOW(),
  updated_at      TIMESTAMP DEFAULT NOW()
);
```

## API エンドポイント
| Method | Path | 説明 |
|--------|------|------|
| GET    | `/api/entries` | 一覧取得（`?search=xxx&category=xxx`）|
| POST   | `/api/entries` | 新規登録（multipart/form-data）|
| GET    | `/api/entries/:id` | 1件取得 |
| PUT    | `/api/entries/:id` | 更新 |
| DELETE | `/api/entries/:id` | 削除 |

## 占いスタジオへの統合時
APIエンドポイント `/api/entries` をFlaskのBlueprintとして
占いスタジオ側に取り込むだけでOK。
