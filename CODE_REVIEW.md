# コードレビュー指摘事項 — BOAT RACE 3連単オッズ収集システム

- レビュー日: 2026-06-11
- 対象リビジョン: `b27125f` (feat: initial 24-venue collector)
- 確認状況: `python -m pytest` → **35 passed**(既存テストはすべて成功)

全体として、単一ワーカー・3秒間隔・raw HTML先行保存・冪等なスナップショット挿入など、
礼節あるスクレイパーとしての設計品質は高い。SQLインジェクションは全箇所パラメータ化済みで問題なし。
認証情報の取り扱いもなく、致命的な脆弱性は見当たらない。
以下は運用継続性・堅牢性を中心とした指摘で、**重要度: 高 / 中 / 低** に分類した。

---

## 重要度: 高

### H-1. クラッシュ後に `running` 状態のジョブが永久に放置される(README の再開保証が破られる)

- 該当: `collector.py` `execute_job()` / `db.py` `due_jobs()` / `daemon.py`
- 内容:
  `execute_job()` は処理開始時に `set_job_status(conn, job_id, "running")` + commit する。
  その直後にプロセスが強制終了(電源断・Windows Update再起動・タスクキル)すると、
  ジョブは `running` のまま残る。しかし `due_jobs()` は
  `WHERE j.status IN ('pending','failed')` しか拾わないため、
  **再起動してもこのジョブは二度と選択されない**。
  README は「異常終了後も再起動すれば未処理ジョブを再開する」と謳っているが、
  この経路では成立しない。
- 修正案: daemon 起動時(および `collect-due` 実行時)にリカバリ処理を入れる。

  ```sql
  UPDATE capture_jobs
     SET status = 'failed',
         last_error = 'recovered from stale running state',
         updated_at_jst = :now
   WHERE status = 'running';
  ```

  単一ワーカー前提なので、起動時点で `running` が残っていれば必ず孤児であり、
  無条件に戻してよい。`attempt_count` は既に消費済みなので二重カウントの心配もない。

### H-2. リトライのバックオフ sleep(最大120秒)が daemon ループ全体をブロックする

- 該当: `collector.py` `collect_due_once()` → `execute_job(..., retry_sleep=True)` / `_fail()`
- 内容:
  daemon は `collect_due_once()` を呼び、その中で `retry_sleep=True` 固定のため、
  あるジョブが失敗すると `time.sleep(15〜120秒)` を**インラインで**実行する。
  単一スレッドなので、その間ほかの場の m02 / final など時間制約が厳しいスロット
  (`SLOT_EXPIRY_MIN` は m02=1分、m05=2分)が処理されず、**期限切れ(expired)で取り逃す**。
  24場同時開催の夕方〜夜は締切が数分おきに重なるため、現実に発生しうる。
- 修正案:
  - daemon 経由では `retry_sleep=False` にし、「次に再試行してよい時刻」
    (`next_retry_at_jst` カラム追加、または `updated_at_jst + backoff` の計算)を
    `due_jobs()` の選択条件に加える方式へ変更する。
  - sleep するのは CLI の `collect-due --once`(単発実行)に限定する。
  - 暫定対応としては、due ジョブを `scheduled_at_jst` 順ではなく
    「期限切れまでの残り時間が短い順」に処理するだけでも被害は減る。

### H-3. `export-parquet` を再実行するとデータが重複蓄積される

- 該当: `export.py` `export_parquet()`
- 内容:
  `df.to_parquet(out_dir, partition_cols=[...])` は pyarrow の
  `write_to_dataset` 相当で、既存パーティションディレクトリに**新しいファイルを追記**する。
  同じ期間で2回エクスポートすると同一行が二重に読める dataset になり、
  解析側で気づきにくい二重計上を引き起こす。
- 修正案: いずれかを実施。
  - pyarrow に直接渡して `existing_data_behavior="delete_matching"` を指定する
    (`pyarrow.dataset.write_dataset` / `pq.write_to_dataset(..., existing_data_behavior=...)`)。
  - エクスポート前に出力対象期間のパーティションを明示的に削除する。
  - 出力先を `parquet/{実行タイムスタンプ}/` にして毎回フルリプレースとし、正本はSQLiteである旨を強調する。

---

## 重要度: 中

### M-1. PIDロックに TOCTOU 競合がある(多重起動防止が完全ではない)

- 該当: `daemon.py` `PidLock.acquire()`
- 内容:
  `exists()` チェック → `write_text()` の間に隙間があり、
  タスクスケジューラの「7:00定時」と「ログオン時」トリガーがほぼ同時に発火した場合など、
  2プロセスが同時に通過しうる。後勝ちで両方が起動し、
  「並列アクセス禁止・3秒間隔」の前提が崩れる(礼節制約への違反につながるため実害が大きい)。
- 修正案: 原子的なファイル作成に変更する。

  ```python
  fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
  os.write(fd, str(os.getpid()).encode("ascii"))
  os.close(fd)
  ```

  `FileExistsError` を捕捉して stale チェック → unlink → 再試行、とすれば
  Windows でも原子性が保たれる。なお stale 判定の PID は再利用されうるため、
  ロックファイルに起動時刻も併記して照合精度を上げるとより安全。

### M-2. ページ識別情報が取れなかったとき mismatch 検証が素通りする

- 該当: `validator.py` `validate_odds_page()` / `parser.py` `_extract_identity()`
- 内容:
  `if page.page_date_yyyymmdd and ...` のように **値が取得できた場合のみ**比較しているため、
  サイトのDOM変更で `_extract_identity()` が全て `None` を返すようになると、
  「URLで要求した日付・場・Rとページ内容が一致」という検証項目7が事実上無効化され、
  取り違えたページを正規スナップショットとして保存しうる。
- 修正案:
  - 識別情報が1つも取れない場合は `ValidationError("mismatch", "page identity not found")`
    として拒否する(120件検証と同格の必須条件にする)。
  - 個別欠落は当面 WARN ログでもよいが、最低限「3要素すべて None」は拒否すべき。
  - 補足: `if page.page_race_no and ...` は `race_no == 0` を偽と扱う。
    実害は薄いが `is not None` 比較が正確。

### M-3. 全場「未開催」の日に discovery が15分毎に24リクエストを打ち続ける

- 該当: `daemon.py` `run_daemon()` の discovery 完了判定
- 内容:
  完了条件が `held` が1件以上あることを要求している
  (`if len(statuses) >= target_count and not failed and held`)。
  まれに全24場が未開催の日(年末年始の谷間・荒天等)があると、
  終日 15分毎 × 24リクエスト ≒ **2,300リクエスト/日**を無駄に発行する。
  低頻度運用の方針に反する。
- 修正案: 「全場のステータスが揃い、failed が無い」なら
  `held == 0` でも discovery 完了とみなす。当日の見逃しが心配なら、
  全場 no_meeting の場合のみ再試行間隔を 15分 → 2〜3時間 に伸ばす。

### M-4. HTTPレスポンスのサイズ上限がない

- 該当: `http_client.py` `PoliteClient.fetch()`
- 内容:
  `resp.content` を無制限に読み込む。経路上の異常(誤ったURL・サーバ側障害・
  巨大なエラーページ)で数百MBの応答が返るとメモリを圧迫し、
  常駐 daemon が巻き添えで落ちる。セキュリティ的にも自衛として上限を設けるのが定石。
- 修正案: `stream=True` でチャンク読みし、合計が上限(例: 5MB)を超えたら
  打ち切って `network_error: response too large` として扱う。
  通常のオッズページは数百KB程度なので 5MB あれば十分。

### M-5. `collect-due` の `--once` フラグが実装と乖離している

- 該当: `cli.py` `cmd_collect_due()` / `build_parser()`
- 内容:
  `--once` を `action="store_true"` で定義しているが、`cmd_collect_due()` は
  `args.once` を一切参照せず、フラグの有無に関わらず同じ動作をする。
  ヘルプを読んだ利用者は「`--once` なしなら常駐する」と誤解しうる。
- 修正案: `--once` を必須扱いにするか削除する。もしくは
  `--once` なしのときは daemon 相当を案内するエラーにする。

### M-6. データディレクトリがカレントディレクトリ依存(`Path.cwd()`)

- 該当: `config.py` `project_root()`
- 内容:
  `BOATRACE_ODDS_HOME` 未設定時は `Path.cwd()` がルートになる。
  別ディレクトリから `python -m boatrace_odds.cli ...` を実行すると
  **そこに新しい空の `data/` とDBが作られ**、「DBが空に見える」「ロックが効かない」
  といった事故になりやすい。PS1スクリプト経由なら安全だが、手動運用時に踏みやすい。
- 修正案:
  - 既定をリポジトリ位置基準(`Path(__file__).resolve().parents[1]`)にする、または
  - `BOATRACE_ODDS_HOME` 未設定時は起動ログに解決済みパスを WARN で明示し、
    意図しない場所に新規DBを作る場合は確認を促す。

### M-7. `export-csv` が全期間スキャン後に Python 側でフィルタしている

- 該当: `export.py` `export_csv()`
- 内容:
  `EXPORT_QUERY + " "` で当日全場を取得してから
  `if r["venue_code"] == venue_code` で絞っている。日数・場数が増えても
  動くが無駄が大きく、`+ " "` という不自然な文字列連結も意図が読めない。
- 修正案: SQL に `AND r.venue_code = ?` を加えてDB側で絞る。

  ```sql
  WHERE r.race_date_jst BETWEEN ? AND ? AND r.venue_code = ?
  ```

### M-8. 締切が後ろ倒しになっても `expired` ジョブは復活しない

- 該当: `db.py` `upsert_job()` / `scheduler.py` `refresh_schedule_from_page()`
- 内容:
  `upsert_job()` は `status == 'pending'` のジョブだけ再スケジュールする。
  天候等で締切が大きく遅延した場合、いったん `expired` / `failed` になったスロットは
  新しい締切時刻では再実行されず、本来取れたはずのスナップショットを欠損する。
- 修正案: 新しい `scheduled_at_jst` が現在より未来で、かつ既存ジョブが
  `expired` または `failed`(snapshot 無し)の場合は `pending` に戻し
  `attempt_count` をリセットする分岐を追加する。

---

## 重要度: 低

### L-1. Windows でのログローテーション失敗の可能性

- 該当: `logging_setup.py`
- daemon 稼働中に `summary_today.ps1` が別プロセスで同じ
  `collector.log` を `RotatingFileHandler` で開く。Windows はファイルを
  開いたままのリネームができないため、ローテーション境界で
  `PermissionError` が起きうる。CLI 側(audit/summary/export)はコンソールのみ、
  またはプロセス別ファイル名にするのが安全。

### L-2. User-Agent の連絡先が実体を持たない

- 該当: `config.py` `USER_AGENT`
- `contact: local user` は連絡先として機能しない。礼節の建前を保つなら
  到達可能なメールアドレス等を入れるか、文言ごと簡素化する。

### L-3. `audit-day` / `summary-day` / `export-*` が `init_db` を呼ばない

- 該当: `cli.py`
- DB未作成の状態で実行すると `connect()` が空のDBファイルを作ったうえで
  `no such table` で落ちる。`init_db(conn)` を通すか、
  DBファイル不存在時に分かりやすいエラーで終了させるとよい。

### L-4. SQLite パフォーマンス PRAGMA

- 該当: `db.py` `connect()`
- WAL 採用済みなら `PRAGMA synchronous = NORMAL` を併用するのが定番
  (WAL + NORMAL はアプリクラッシュ耐性を保ったまま書き込みを大幅に高速化)。
  また `PRAGMA busy_timeout` は `timeout=30` 引数で設定済みなので現状でよい。

### L-5. `fetch_attempts` の調査用インデックス

- 該当: `schema.sql`
- 障害調査で `outcome` 別・日付別に攻めることが多いなら
  `CREATE INDEX idx_attempts_outcome ON fetch_attempts (outcome, requested_at_jst);`
  を足しておくと、データが溜まった後の調査が楽になる。

### L-6. `load_raw_html` の相対パス解決

- 該当: `storage.py` `load_raw_html()`
- DB由来の相対パスを `project_root() / p` でそのまま結合している。
  ローカル単独運用ではリスクは小さいが、DBを外部から受け取る運用に変わった場合
  `..` を含むパスで意図外のファイルを読める。
  `resolve()` 後に `raw_html_dir()` 配下であることを検証する1行を入れておくと堅い。

### L-7. PowerShell スクリプトのハードコードパス

- 該当: `start_daily_all_venues.ps1` / `summary_today.ps1`
- `C:\Users\みすず\.cache\codex-runtimes\...\python.exe` の決め打ちは
  環境移行時に静かに `python`(PATH任せ)へフォールバックして
  別バージョンで動く危険がある。フォールバック時に WARN を出力するとよい。
  また README 記載の `cd C:\Users\みすず\Desktop\boatrace_db\boat_db_8min` も
  リポジトリ名(`boat_db_24venues`)と食い違っており、README の更新を推奨。

### L-8. タスクスケジューラ登録の停止制御

- 該当: `register_daily_start_task.ps1`
- ログオントリガーで起動した daemon は「夜に止める」操作が手動前提。
  `-ExecutionTimeLimit`(例: 18時間)を `New-ScheduledTaskSettingsSet` に
  指定しておくと、止め忘れても深夜に自動停止できる。

### L-9. DBバックアップ手順がない

- SQLite が正本である以上、日次で
  `sqlite3 data/boatrace_odds.sqlite3 ".backup data/backup/odds_YYYY-MM-DD.sqlite3"`
  相当(WAL 安全なオンラインバックアップ)を summary_today.ps1 の末尾に
  足しておくことを推奨。raw HTML は再ダウンロード不能な一次資料なので
  こちらも定期アーカイブ対象にする価値がある。

---

## セキュリティ総評

| 観点 | 評価 |
|---|---|
| SQLインジェクション | 問題なし(全クエリでプレースホルダ使用) |
| 認証情報・秘密情報 | 取り扱いなし(設計通り) |
| TLS検証 | requests 既定の検証が有効(無効化箇所なし) |
| 外部入力の検証 | 120件・6P3網羅・識別一致など多層検証あり。ただし M-2 の素通り経路に注意 |
| リソース枯渇耐性 | M-4(応答サイズ無制限)のみ要対応 |
| パストラバーサル | L-6 の防御的1行を推奨(現運用では実害なし) |
| 多重起動・レート制御 | 設計は良好。M-1(ロックの原子性)で仕上げる |

## 対応優先度まとめ

1. **H-1**: 起動時に `running` ジョブを回収(数行で直り、データ欠損を直接防ぐ)
2. **H-2**: daemon 経路のインライン sleep 廃止(時間制約スロットの取り逃し防止)
3. **H-3**: Parquet 再エクスポートの重複対策(解析の信頼性)
4. **M-1 / M-2 / M-3 / M-4**: 堅牢性・礼節・自衛の仕上げ
5. その他 M / L: 運用の中で順次
