# BOAT RACE 3連単オッズ収集システム

Windows 11 上で動作する Python 製ローカル収集システム。
BOAT RACE 公式サイトの 3連単オッズ(120通り)を、締切前後の時系列スナップショットとして SQLite に保存する。

- 対象場: BOAT RACE 全24場(01〜24)
- 収集スロット: 締切20分前(m20)・12分前(m12)・8分前(m08)・5分前(m05)・2分前(m02)・締切時(final)
- 自動投票なし / ログインなし / 認証情報なし / 高頻度アクセスなし

## セットアップ (Windows 11)

```bat
py -3 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m boatrace_odds.cli init-db
```

Python 3.10 以上。`tzdata` を含むため Windows でも `Asia/Tokyo` が解決される。

## 使い方

```bat
:: DB初期化(冪等)
python -m boatrace_odds.cli init-db

:: 当日の開催情報・締切予定時刻を登録(各場1Rページを1回ずつ取得)
python -m boatrace_odds.cli discover-day --date 2026-06-10

:: 期限が来たジョブを1回処理して終了
python -m boatrace_odds.cli collect-due --once

:: 常駐モード(10秒毎にローカルキューを確認。日付変更で自動discover)
python -m boatrace_odds.cli daemon

:: 欠損状況の確認
python -m boatrace_odds.cli audit-day --date 2026-06-10

:: 解析用エクスポート
python -m boatrace_odds.cli export-parquet --date-from 2026-06-01 --date-to 2026-06-10
python -m boatrace_odds.cli export-csv --date 2026-06-10 --venue-code 01
```

通常運用は「朝に `daemon` を起動して夜に止める」だけでよい。
`daemon` は多重起動を PID ロックで防止し、異常終了後も再起動すれば未処理ジョブを再開する。

全24場の毎日運用は PowerShell で以下を起動する。

```powershell
cd C:\Users\みすず\Desktop\boatrace_db\boat_db_8min
powershell -NoProfile -ExecutionPolicy Bypass -File .\start_daily_all_venues.ps1
```

`BOATRACE_ODDS_VENUES` を指定しない限り全24場を確認する。日初の discovery では
各場の1Rページを1回ずつ取得し、開催場だけに収集ジョブを作成する。未開催場は
`venue_day_status.status = 'no_meeting'` としてDBに残す。

当日の集計確認:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\summary_today.ps1
```

Windows Update 等の再起動後も自動復帰させる場合は、タスクスケジューラへ登録する。
毎日7:00とログオン時に起動し、すでにdaemonが動作中ならPIDロックで多重起動を防ぐ。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\register_daily_start_task.ps1
```

## アクセス制御(実装済みの制約)

| 項目 | 値 |
|---|---|
| ワーカー数 | 1(並列アクセス禁止) |
| リクエスト間隔 | 3秒以上 |
| タイムアウト | 接続5秒 / 読込15秒 |
| 再試行 | 最大3回(間隔 15s → 45s → 120s) |
| HTTP 403/429 | その日の自動収集を停止(CRITICALログ) |
| User-Agent | 明示(連絡先付き) |

アクセス制限を回避する処理は実装していない。

## データ設計

正本は SQLite (`data/boatrace_odds.sqlite3`)。CSV/Parquet は派生物。

| テーブル | 内容 |
|---|---|
| `races` | 1レース(日付×場×R) |
| `venue_day_status` | 日付×場の開催判定(`held` / `no_meeting` / `discovery_failed`) |
| `race_schedule_observations` | 締切予定時刻の観測履歴 |
| `capture_jobs` | レース×スロットの収集予定 |
| `fetch_attempts` | HTTPアクセス試行履歴(再試行ごとに1行、削除しない) |
| `odds_snapshots` | 検証済みオッズ集合(1ジョブ最大1件 = 冪等) |
| `trifecta_odds` | 1スナップショット120行の縦持ちオッズ |

- オッズは表示文字列(`odds_text`)と **表示値×10の整数**(`odds_tenths`、float不使用)で保存
- `欠場` 等は `odds_tenths = NULL`(明示的欠損)
- 全時刻は `Asia/Tokyo` 付き ISO-8601(`...+0900`)
- raw HTML は解析前に gzip 保存: `data/raw/html/{YYYY}/{MM}/{DD}/{場}/{R}R/{slot}_{ts}.html.gz`
- 検証失敗時もraw HTMLは削除せず、`fetch_attempts` に `parse_error` / `incomplete` / `mismatch` を記録

## 保存前検証

1. 3連単オッズが120件
2. 重複組み合わせなし
3. 1〜6号艇のみ
4. 同一艇の重複着順なし
5. 6P3=120通りを完全網羅
6. オッズが正の値または明示的欠損
7. URLで要求した日付・場・Rとページ内容が一致

## Parquet 出力

`trifecta_odds` 中心の縦長形式。`year` / `month` / `venue`(hiveパーティション)で分割。
場コードの先頭ゼロを保持するため、ファイル内に文字列カラム `venue_code` を必ず含める。

## テスト

```bat
python -m pytest
```

fixture(実ページ由来): 販売中 / 締切時 / データなし / 意図的不完全ページ。
35テストで、120件抽出・final判別・no_data判別・119件拒否・冪等性・
開催/未開催判定・試行履歴の増加・raw HTML先行保存・先頭ゼロ保持・
JST保存・Parquet出力を検証。

## 注意

- 公式サイトのHTML構造変更時は `boatrace_odds/parser.py` のみ修正すればよい(DOM依存処理を集約)
- robots.txt とサイトポリシーを尊重し、低頻度・単一ワーカーで運用すること
