# プロジェクトルール（歩掛JSONテストケース自動生成エンジン）

## ★ 参照順ルール（テストケース精度向上時・必須）

Gaia の仕様を調べるときは、必ず次の順で参照する:

1. **`共通/source_ref/spec/歩掛JSON内部仕様書.md`**（まずここ。確度ラベル付きの知識集約）
2. **Sirius ソース**（仕様書に無い/根拠が弱い事項のみ）:
   フルクローン `C:\Users\imoo\sirius`（**読み取り専用・書き込み禁止**。bashからは
   /sessions/<session>/mnt/sirius）を第一参照とする（2026-06-11 接続）。
   旧 `共通/source_ref/sirius/` は部分コピー（参考用に保管）。

**ソースを見てわかったことは、必ず 歩掛JSON内部仕様書.md へ追記する**（確度ラベル
[確定/ソース]・出典ファイル名付き）。仕様書を「育てる」ことで以後のソース参照を減らす。

- 要検証事項（付録A）は工種展開・実機フィードバックで順次埋める方針（2026-06-05 決定）。
  確定したら確度ラベルを更新する。
- 仕様書の実データ由来の主張は `共通/source_ref/spec/check_spec_assumptions.py` で
  機械チェックできる。**新工種の JSON を追加したら必ず実行**し、WARN/NG は仕様書へ追補・補正。

## 主要ドキュメント

| 文書 | 役割 |
|---|---|
| `共通/spec/汎用テストケース生成仕様.md` | 生成エンジン（実装側）の仕様。実装判定ルールの正 |
| `共通/source_ref/spec/歩掛JSON内部仕様書.md` | 製品（Gaia）側の仕様。ソース由来知識の集約（ローカルのみ・git管理外） |
| `docs/タスク一覧.md` | 進捗・バックログ管理 |
| `docs/ロードマップ_汎用テストケース生成エンジンへの道.md` | 長期方針 |

## 機密・git 管理

- `共通/source_ref/` は機密（git-ignored）。内部仕様書もこの配下に置きローカルのみで管理。
- 進捗報告/ も git-ignored（回帰ベースライン `進捗報告/regression_baseline/` を含む）。

## ★ 回帰テストルール（必須）

エンジン/step系スクリプト（`共通/engine/` `共通/step1_5_check/` `共通/step2_proposals/`
`共通/step3_csv/` `共通/pipeline.py`）を改修したら、**コミット前に必ず**実行する:

```bash
python3 共通/regression/run_regression.py
```

- 全工種約4秒。結果は `進捗報告/regression_report_最新.md` にも保存される。
- DIFF が出たら「意図した変更のみか」をユーザに確認し、**承認後にのみ**
  `--update-baseline <工種id>` でベースラインを更新する（勝手に更新しない）。
- 合格工種の追加: `共通/regression/targets.yaml` にエントリ追加 →
  `--update-baseline <id>` でスナップショット取得（それだけで回帰対象になる）。
- 旧 verify_all.sh は引退（参考用に保管）。

## 運用上の注意

- **Edit ツールの末尾 truncate バグ**: 長いファイルを Edit した後は必ず構文チェック＋ tail 確認
  （詳細は docs/タスク一覧.md フェーズ2.7.5 の記載）。切れていたら Write で全体を書き直す。
- yaml（`共通/knowledge/global_rules.yaml`）への追加は必ず人間に確認。
