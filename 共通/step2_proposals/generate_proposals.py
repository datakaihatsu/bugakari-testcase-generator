"""
② テスト提案リスト生成
step1_差分レポート.csv + 新JSON → step2_提案リスト.csv

出力列:
  提案ID     : P-001, P-002, ...
  テスト区分  : 差分 / 回帰 / 情報
  根拠カテゴリ: 代価表 / 計算表 / 質問 / 質問設定 / 選択肢 / フロー / -
  根拠ID     : 差分レポートのID列と対応
  差分概要   : 何が変わったか
  テスト軸   : どの質問を操作してテストするか（UI表示名 or 任意）
  入力値     : 具体的な選択値（or 任意 or 既存選択肢）
  期待確認内容: 何を確認するか
  備考       : フロー遷移先・旧値など補足

使い方:
  python generate_proposals.py <diff_csv> <new_json> <output_csv> [old_json]
"""

import sys
import os
import csv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'engine'))
from bugakari_json import BugakariJSON

HEADER = [
    '提案ID', 'テスト区分', '根拠カテゴリ', '根拠ID',
    '差分概要', 'テスト軸', '入力値', '期待確認内容', '備考',
]


class ProposalGenerator:

    def __init__(self, diff_csv_path, new_json_path, old_json_path=None):
        self.new_json = BugakariJSON(new_json_path)
        self.old_json = BugakariJSON(old_json_path) if old_json_path else None
        self.diff_rows = self._load_diff(diff_csv_path)
        self._proposals = []
        self._counter = 0
        self._regression_axes = {}  # テスト軸名 → 質問No (差分テストで使ったテスト軸を収集)

    # ------------------------------------------------------------------
    # CSV読み込み
    # ------------------------------------------------------------------

    def _load_diff(self, path):
        rows = []
        with open(path, encoding='cp932', newline='') as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(row)
        return rows

    # ------------------------------------------------------------------
    # 提案追加ヘルパー
    # ------------------------------------------------------------------

    def _next_id(self):
        self._counter += 1
        return f'P-{self._counter:03d}'

    def _add(self, kubun, cat, rid, summary, axis, input_val, expected, note=''):
        self._proposals.append([
            self._next_id(), kubun, cat, rid,
            summary, axis, input_val, expected, note,
        ])

    def _register_regression_axis(self, axis_name, rid=''):
        """差分テストで使ったテスト軸を回帰テスト候補として登録"""
        if axis_name not in ('任意', '（各選択肢）', ''):
            self._regression_axes.setdefault(axis_name, rid)

    # ------------------------------------------------------------------
    # メイン生成ロジック
    # ------------------------------------------------------------------

    def generate(self):
        # SF固定値の確認 → 省庁区分テスト軸の要否
        sf_fixed = self.new_json.is_province_auto_selected()
        if sf_fixed:
            province = self.new_json.resolve_province_name() or '（不明）'
            self._add(
                '情報', '-', '-',
                'SF固定値確認',
                '省庁区分', '（自動選択）',
                '省庁区分はテストケースの列に含めない',
                f'SF固定値→{province}専用JSON。UIに省庁選択画面は表示されない',
            )

        for row in self.diff_rows:
            cat = row['カテゴリ']
            kind = row['変更種別']
            rid = row['ID']
            name = row['名称']
            old_val = row['旧値']
            new_val = row['新値']
            note = row['備考']

            if cat == '代価表':
                self._handle_daika(kind, rid, name, old_val, new_val, note)
            elif cat == '計算表':
                self._handle_keisan(kind, rid, name, old_val, new_val, note)
            elif cat == '質問':
                self._handle_sitsumon(kind, rid, name, old_val, new_val, note)
            elif cat == '質問設定':
                self._handle_sitsumon_setting(kind, rid, name, old_val, new_val, note)
            elif cat == '選択肢':
                self._handle_choice(kind, rid, name, old_val, new_val, note)
            elif cat == 'フロー':
                self._handle_flow(kind, rid, name, old_val, new_val, note)

        # 差分テストで使ったテスト軸ごとに回帰テスト1件追加
        for axis_name in sorted(self._regression_axes.keys()):
            self._add(
                '回帰', '-', '-',
                '既存パスへの影響なし確認',
                axis_name, '既存の選択肢',
                '旧バージョンと同じ結果になること',
                '差分テスト軸の既存値での回帰確認',
            )

        return self._proposals

    # ------------------------------------------------------------------
    # 代価表（DaikaItem / DaikaItemLine）
    # ------------------------------------------------------------------

    def _handle_daika(self, kind, rid, name, old_val, new_val, note):
        if kind == '追加':
            self._add(
                '差分', '代価表', rid,
                f'代価表「{name}」追加',
                '任意', '任意',
                f'代価表「{name}」が{new_val}で表示されること（目視確認）',
                '新規代価表の表示確認',
            )
        elif kind == '変更':
            if note == '行数変更':
                self._add(
                    '差分', '代価表', rid,
                    f'代価表「{name}」行数変更 {old_val}→{new_val}',
                    '任意', '任意',
                    f'代価表「{name}」の行数が{new_val}になっていること',
                    '行数変更の目視確認',
                )
            elif note == '備考変更':
                self._add(
                    '差分', '代価表', rid,
                    f'代価表「{name}」備考変更',
                    '任意', '任意',
                    f'代価表「{name}」の備考が「{new_val}」になっていること（目視確認）',
                    f'備考変更: 「{old_val}」→「{new_val}」',
                )
            else:
                self._add(
                    '差分', '代価表', rid,
                    f'代価表「{name}」変更 ({note})',
                    '任意', '任意',
                    f'代価表「{name}」が正しく変更されていること',
                    f'{old_val}→{new_val}',
                )
        elif kind == '削除':
            self._add(
                '差分', '代価表', rid,
                f'代価表「{name}」削除',
                '任意', '任意',
                f'代価表「{name}」が表示されないこと',
                '削除代価表の非表示確認',
            )

    # ------------------------------------------------------------------
    # 計算表（KeisanItem）
    # ------------------------------------------------------------------

    def _handle_keisan(self, kind, rid, name, old_val, new_val, note):
        if kind == '変更':
            self._add(
                '差分', '計算表', rid,
                f'計算表変数「{name}」変更',
                '任意', '任意',
                f'「{name}」を含む計算結果が正しいこと',
                f'{note}: {old_val}→{new_val}',
            )
        elif kind == '追加':
            self._add(
                '差分', '計算表', rid,
                f'計算表変数「{name}」追加',
                '任意', '任意',
                f'「{name}」を使う計算結果が正しいこと',
                f'新規変数: {new_val}',
            )
        elif kind == '削除':
            self._add(
                '差分', '計算表', rid,
                f'計算表変数「{name}」削除',
                '任意', '任意',
                f'「{name}」を使っていた計算に影響がないこと',
                f'削除変数: {old_val}',
            )

    # ------------------------------------------------------------------
    # 質問（SitsumonItem）
    # ------------------------------------------------------------------

    def _handle_sitsumon(self, kind, rid, name, old_val, new_val, note):
        sitsumon_no = self._parse_sitsumon_no(rid)

        if kind == '追加':
            choices = self.new_json.get_sitsumon_choices(sitsumon_no) if sitsumon_no else []
            if choices:
                for choice in choices:
                    self._add(
                        '差分', '質問', rid,
                        f'質問「{name}」新規追加',
                        name, choice,
                        f'「{choice}」を選択したとき正しく動作すること',
                        '新規追加質問の各選択肢を確認',
                    )
                self._register_regression_axis(name, rid)
            else:
                self._add(
                    '差分', '質問', rid,
                    f'質問「{name}」新規追加',
                    name, '（各選択肢）',
                    f'「{name}」が正しく表示・動作すること',
                    f'SitsumonKind: {new_val}',
                )
                self._register_regression_axis(name, rid)

        elif kind == '削除':
            self._add(
                '差分', '質問', rid,
                f'質問「{name}」削除',
                '任意', '任意',
                f'「{name}」が表示されないこと',
                '削除質問の非表示確認',
            )

        elif kind == '変更':
            self._add(
                '差分', '質問', rid,
                f'質問「{name}」表示名変更',
                name, '任意',
                f'質問の表示名が「{new_val}」になっていること',
                f'表示名変更: 「{old_val}」→「{new_val}」',
            )

    # ------------------------------------------------------------------
    # 質問設定（Sitsumon017 / Sitsumon014）
    # ------------------------------------------------------------------

    def _handle_sitsumon_setting(self, kind, rid, name, old_val, new_val, note):
        self._add(
            '差分', '質問設定', rid,
            f'質問設定「{name}」{kind}',
            '任意', '任意',
            f'「{name}」の設定変更が計算結果に正しく反映されること',
            f'{note}: {old_val}→{new_val}',
        )

    # ------------------------------------------------------------------
    # 選択肢（Sitsumon019.SitTabRows）
    # ------------------------------------------------------------------

    def _handle_choice(self, kind, rid, name, old_val, new_val, note):
        if kind == '追加':
            self._add(
                '差分', '選択肢', rid,
                f'選択肢「{new_val}」追加（質問:「{name}」）',
                name, new_val,
                f'「{new_val}」が選択肢に表示され、選択したとき正しく動作すること',
                '新規選択肢の確認',
            )
            self._register_regression_axis(name, rid)
        elif kind == '削除':
            self._add(
                '差分', '選択肢', rid,
                f'選択肢「{old_val}」削除（質問:「{name}」）',
                name, '任意',
                f'「{old_val}」が選択肢に表示されないこと',
                '削除選択肢の非表示確認',
            )

    # ------------------------------------------------------------------
    # フロー（FlowItems）
    # ------------------------------------------------------------------

    def _handle_flow(self, kind, rid, name, old_val, new_val, note):
        box_no = self._parse_box_no(rid)

        if kind == '追加':
            next_steps = (
                self.new_json.resolve_callbox_names(box_no) if box_no else []
            )
            next_display = '、'.join(next_steps) if next_steps else '（遷移先なし）'
            self._add(
                '差分', 'フロー', rid,
                f'フロー「{name}」追加（{rid}）',
                name, '（表示された質問に回答）',
                f'「{name}」を経由後「{next_display}」へ遷移すること',
                'フロー追加の遷移確認',
            )
            self._register_regression_axis(name, rid)

        elif kind == '変更':
            self._add(
                '差分', 'フロー', rid,
                f'フロー「{name}」変更（{rid}）',
                name, '（表示された質問に回答）',
                f'「{name}」の遷移先が正しいこと',
                f'フロー変更: {old_val}→{new_val}',
            )
            self._register_regression_axis(name, rid)

        elif kind == '削除':
            self._add(
                '差分', 'フロー', rid,
                f'フロー「{name}」削除（{rid}）',
                '任意', '任意',
                f'「{name}」へのフロー遷移が発生しないこと',
                'フロー削除の確認',
            )

    # ------------------------------------------------------------------
    # ユーティリティ
    # ------------------------------------------------------------------

    def _parse_sitsumon_no(self, rid):
        if rid.startswith('質問No:'):
            try:
                return int(rid.replace('質問No:', ''))
            except ValueError:
                pass
        return None

    def _parse_box_no(self, rid):
        if rid.startswith('BoxNo:'):
            try:
                return int(rid.replace('BoxNo:', ''))
            except ValueError:
                pass
        return None


# ------------------------------------------------------------------
# エントリポイント
# ------------------------------------------------------------------

def run(diff_csv_path, new_json_path, output_path, old_json_path=None):
    generator = ProposalGenerator(diff_csv_path, new_json_path, old_json_path)
    proposals = generator.generate()

    BugakariJSON.write_csv([HEADER] + proposals, output_path)

    info_count = sum(1 for p in proposals if p[1] == '情報')
    diff_count = sum(1 for p in proposals if p[1] == '差分')
    reg_count = sum(1 for p in proposals if p[1] == '回帰')

    print(f'提案リスト生成完了: {output_path}')
    print(f'  合計: {len(proposals)}件（情報{info_count} / 差分{diff_count} / 回帰{reg_count}）')
    for cat in ['代価表', '計算表', '質問', '質問設定', '選択肢', 'フロー', '-']:
        n = sum(1 for p in proposals if p[2] == cat and p[1] != '情報')
        if n:
            print(f'  {cat}: {n}件')


if __name__ == '__main__':
    if len(sys.argv) < 4:
        print('Usage: python generate_proposals.py <diff_csv> <new_json> <output_csv> [old_json]')
        sys.exit(1)
    old_json = sys.argv[4] if len(sys.argv) > 4 else None
    run(sys.argv[1], sys.argv[2], sys.argv[3], old_json)
