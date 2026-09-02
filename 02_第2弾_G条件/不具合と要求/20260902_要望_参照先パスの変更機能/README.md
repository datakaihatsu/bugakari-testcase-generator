# 要望: 参照先（GaiaCloud データ）のパスを変更できるようにしたい

- 受領日: 2026-09-02
- 依頼元: 協力会社（利用者8名）
- 内容: 8名のうち1名が **GaiaCloud のデータを外付けSSDに移動して運用**しており、
  参照元パスが固定の現仕様ではツールが使えない。参照先フォルダを任意に変更できる機能がほしい。
- 対象版: v1.1.2 以前（`webapp/config.json` を手で書き換えるしか手段が無かった）
- 対応版: **v1.2.0**（2026-09-02）

## 方針（ユーザ指示）

> 基本は参照パスを固定にしておいて、画面の目立たないところでパスを変更できるように

- 既定は従来どおり `C:/ProgramData/CoBeing/GaiaCloud/DB`。通常の8名の運用・手順は**一切変わらない**。
- 変更UIはヘッダーの格納場所表示の右にある小さな **「変更」** リンクのみ（普段は閉じている）。

## 実装

| 層 | 追加したもの |
|---|---|
| `webapp/service.py` | `user_settings_path` / `load_user_settings` / `save_user_settings` / `clear_user_settings` / `normalize_setting_paths` / `derive_from_root` / `check_paths` / `settings_state`、`load_config(use_user_settings=)` |
| `webapp/server.py` | `GET /api/settings`、`POST /api/settings`（`action: derive/check/save/reset`）、`_reload_cfg()` |
| `webapp/static/*` | ヘッダーの「変更」リンク＋設定パネル（`#settingsPanel`）、変更中バッジ |

### 設定の保存先（重要）

`%LOCALAPPDATA%\GaiaKoshuTC\settings.json`（配布フォルダの**外**）。

配布zipの更新は「既存zipを展開 → `app/` を最新masterで上書き」方式のため、
`app/webapp/config.json` に書くと**版を上げるたびにユーザ設定が消える**。
ユーザ領域に置くことで、ツールを新しい版に入れ替えても設定が残る。
（テスト時は環境変数 `GAIA_TC_SETTINGS_DIR` で保存先を差し替えられる）

保存されるキーは `expcd_path` / `bugakari_root` の2つだけ。壊れていれば無視して既定で動く。

### 使い勝手

- **フォルダ1つ指定でよい**: 「データフォルダ（DB）」に `E:\CoBeing\GaiaCloud\DB` のような
  フォルダを入れて「このフォルダから設定」を押すと、その下（自身 / `DB` / `GaiaCloud/DB` /
  `CoBeing/GaiaCloud/DB` の順に探索）から `Common/ExpCDConvert.json` と `Bugakari` を自動判定する。
- 個別指定も可能（`<details>` の中に畳んである）。
- 保存前に実在チェック。**存在しないパスは既定では保存させない**が、
  外付けドライブ未接続のまま設定だけ入れたいケースのために
  「確認できないまま保存する」ボタンで通せる（保存後の表示にも未確認である旨を出す）。
- 保存すると**サーバ再起動なしで即反映**（`CFG` の中身を差し替える）。ヘッダーに「変更中」バッジ。
- 「既定に戻す」で設定ファイルを削除し、配布既定に復帰。
- サーバの黒画面にも、既定以外を見ているときだけ1行出る（問い合わせ時の切り分け用）。

## 検証

- `webapp` 全テスト 72件 OK（`test_service.py` にユーザ設定・パス推定の8ケース、
  `test_server.py` に `/api/settings` のE2E 1ケースを追加）。
- 回帰 `regression/run_regression.py` 全工種一致（生成ロジックは無変更）。
- 実機（ブラウザ）で 推定→保存→`/api/locate` の探索先が新パスに切替→サーバ再起動後も保持→
  既定に戻す、まで確認。
