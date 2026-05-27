"""
③ テストケースCSV生成（シナリオベース）
step2_提案リスト.csv → step3_テストケース.csv

【設計方針】
「変更点1件→TC1件」ではなく、変更点を意味のある単位でグループ化して
「確認シナリオ1件→TC1件」で生成する。

グループ化ルール:
  - 情報行（SF固定値メモ等）は除外する
  - 計算表の変更: まとめて1件（複数変数変更でも「計算式が変わった」は1シナリオ）
  - 質問設定の変更: まとめて1件
  - 選択肢の変更: テスト軸（質問名）ごとに1件
  - 質問の変更: 変更種別（追加/変更/削除）ごとに1件
  - フローの変更: 変更種別（追加/変更/削除）ごとに1件
  - 代価表の変更: 1件ずつ（内容が工種依存で異なるため個別）
  - 回帰: テスト軸が同じ提案は1件に集約

出力列:
  テストID    : TC-001, TC-002, ...
  テスト区分   : 差分 / 回帰
  変更概要    : 何の差分に対するテストか（複数変更はまとめて記載）
  入力条件    : 確認に必要な入力値（シナリオ）
  期待確認内容 : 何を確認するか
  確認方法    : 目視確認 / 計算値確認 / フロー遷移確認

使い方:
  python generate_csv.py <proposals_csv> <output_csv>
"""

import sys
import os
import csv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'engine'))
from bugakari_json import BugakariJSON

HEADER = [
    'テストID', 'テスト区分', '変更概要', '入力条件', '期待確認内容', '確認方法',
]


def _load_proposals(path):
    rows = []
    with open(path, encoding='cp932', newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows


def _get_change_kind(summary):
    """差分概要文字列から変更種別（追加/削除/変更）を推定"""
    if '新規追加' in summary or ('追加' in summary and '削除' not in summary):
        return '追加'
    if '削除' in summary:
        return '削除'
    return '変更'


def _make_condition(axis, input_val):
    if not axis or axis == '任意':
        return '（任意の入力値で確認）'
    if not input_val or input_val in ('任意', '（各選択肢）', '既存の選択肢', '（表示された質問に回答）'):
        return f'{axis}: （表示された選択肢から回答）'
    return f'{axis}: {input_val}'


def _join_expected(props, max_items=20):
    """複数提案の期待確認内容を箇条書きにまとめる"""
    lines = list(dict.fromkeys(p['期待確認内容'] for p in props))
    text = '\n'.join(f'・{e}' for e in lines[:max_items])
    if len(lines) > max_items:
        text += f'\n（他{len(lines) - max_items}件）'
    return text


def _join_summaries(props, max_items=20):
    """複数提案の差分概要を箇条書きにまとめる"""
    lines = list(dict.fromkeys(p['差分概要'] for p in props))
    text = '\n'.join(f'・{s}' for s in lines[:max_items])
    if len(lines) > max_items:
        text += f'\n（他{len(lines) - max_items}件）'
    return text


def generate(proposals):
    """
    提案リストからシナリオベースのテストケースを生成する。
    戻り値: [HEADER] + [[テストID, テスト区分, ...], ...]
    """
    diff_props = [p for p in proposals if p['テスト区分'] == '差分']
    reg_props  = [p for p in proposals if p['テスト区分'] == '回帰']

    rows = []
    counter = 0

    def add(kubun, summary, condition, expected, method):
        nonlocal counter
        counter += 1
        rows.append([
            f'TC-{counter:03d}', kubun, summary, condition, expected, method,
        ])

    # ----------------------------------------------------------------
    # 代価表変更: 1件ずつ（内容が異なるため個別）
    # ----------------------------------------------------------------
    for p in diff_props:
        if p['根拠カテゴリ'] != '代価表':
            continue
        add('差分', p['差分概要'], '（任意の入力値で確認）',
            p['期待確認内容'], '目視確認（行数・備考）')

    # ----------------------------------------------------------------
    # 計算表変更: まとめて1件
    # ----------------------------------------------------------------
    keisan = [p for p in diff_props if p['根拠カテゴリ'] == '計算表']
    if keisan:
        summary = f'計算式変更（{len(keisan)}件）: ' + '、'.join(p['差分概要'] for p in keisan)
        expected = _join_expected(keisan)
        notes = [p['備考'] for p in keisan if p.get('備考')]
        if notes:
            expected += '\n変更内容: ' + ' / '.join(notes)
        add('差分', summary, '（任意の入力値で確認）', expected, '計算値確認')

    # ----------------------------------------------------------------
    # 質問設定変更: まとめて1件
    # ----------------------------------------------------------------
    settings = [p for p in diff_props if p['根拠カテゴリ'] == '質問設定']
    if settings:
        summary = f'質問設定変更（{len(settings)}件）: ' + '、'.join(p['差分概要'] for p in settings)
        expected = _join_expected(settings)
        add('差分', summary, '（任意の入力値で確認）', expected, '計算値確認')

    # ----------------------------------------------------------------
    # 選択肢変更: テスト軸（質問名）ごとに1件
    # ----------------------------------------------------------------
    choices = [p for p in diff_props if p['根拠カテゴリ'] == '選択肢']
    axis_groups = {}
    for p in choices:
        axis_groups.setdefault(p['テスト軸'], []).append(p)
    for axis, group in axis_groups.items():
        # 追加と削除を分けて表現
        added   = [p for p in group if _get_change_kind(p['差分概要']) == '追加']
        deleted = [p for p in group if _get_change_kind(p['差分概要']) == '削除']
        # 追加された選択肢の値リスト
        added_vals = [p['入力値'] for p in added if p['入力値'] and p['入力値'] not in ('任意',)]
        if added_vals:
            condition = f'{axis}: {" / ".join(added_vals)}（追加された選択肢を確認）'
        else:
            condition = _make_condition(axis, None)
        expected_parts = []
        if added:
            expected_parts.append('【追加】' + '、'.join(p['差分概要'] for p in added))
        if deleted:
            expected_parts.append('【削除】' + '、'.join(p['差分概要'] for p in deleted))
        expected = '\n'.join(expected_parts)
        summary = f'選択肢変更（{len(group)}件）: {axis}'
        add('差分', summary, condition, expected, '目視確認（選択肢表示）')

    # ----------------------------------------------------------------
    # 質問変更: 変更種別（追加/変更/削除）ごとに1件
    # ----------------------------------------------------------------
    sitsumons = [p for p in diff_props if p['根拠カテゴリ'] == '質問']
    _add_by_kind(sitsumons, '質問', '目視確認（質問・選択肢表示）', add)

    # ----------------------------------------------------------------
    # フロー変更: 変更種別（追加/変更/削除）ごとに1件
    # ----------------------------------------------------------------
    flows = [p for p in diff_props if p['根拠カテゴリ'] == 'フロー']
    _add_by_kind(flows, 'フロー', 'フロー遷移確認', add)

    # ----------------------------------------------------------------
    # 回帰テスト: テスト軸が同じ提案は1件に集約
    # ----------------------------------------------------------------
    seen_axes = set()
    for p in reg_props:
        axis = p['テスト軸']
        if axis in seen_axes:
            continue
        seen_axes.add(axis)
        condition = _make_condition(axis, p['入力値'])
        add('回帰', p['差分概要'], condition, p['期待確認内容'], '計算値確認')

    return [HEADER] + rows


def _add_by_kind(props, cat_label, default_method, add_fn):
    """
    変更種別（追加/変更/削除）でグループ化し、種別ごとに1件のTCを生成する。
    追加/削除はリスト形式で列挙。変更は変更後の状態の確認。
    """
    kind_groups = {'追加': [], '変更': [], '削除': []}
    for p in props:
        kind = _get_change_kind(p['差分概要'])
        kind_groups[kind].append(p)

    for kind in ('追加', '変更', '削除'):
        group = kind_groups[kind]
        if not group:
            continue
        count = len(group)

        if kind == '追加':
            summary = f'{cat_label}追加（{count}件）'
            condition = '追加された項目を含む操作を実施して確認'
            expected = f'以下が新規追加されていること:\n' + _join_summaries(group)
        elif kind == '削除':
            summary = f'{cat_label}削除（{count}件）'
            condition = '削除された項目の操作経路を確認'
            expected = f'以下が削除されていること:\n' + _join_summaries(group)
        else:
            summary = f'{cat_label}変更（{count}件）'
            condition = '変更された項目を含む操作を実施して確認'
            expected = f'以下の変更が正しく反映されていること:\n'
            for p in group[:20]:
                expected += f'・{p["差分概要"]}: {p["期待確認内容"]}\n'
            if count > 20:
                expected += f'（他{count - 20}件）'
            expected = expected.rstrip()

        add_fn('差分', summary, condition, expected, default_method)


def run(proposals_csv_path, output_path):
    proposals = _load_proposals(proposals_csv_path)
    rows = generate(proposals)

    BugakariJSON.write_csv(rows, output_path)

    diff_count = sum(1 for r in rows[1:] if r[1] == '差分')
    reg_count  = sum(1 for r in rows[1:] if r[1] == '回帰')
    total = len(rows) - 1
    print(f'テストケースCSV生成完了: {output_path}')
    print(f'  合計: {total}件（差分{diff_count} / 回帰{reg_count}）')


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print('Usage: python generate_csv.py <proposals_csv> <output_csv>')
        sys.exit(1)
    run(sys.argv[1], sys.argv[2])
