"""
02_605773 大型ブレーカ(ベースマシン含む) テストケース生成
32-1951 / 2018→2026 改定対応

仕様: ../spec/context_確定仕様.md
"""

import sys
import os

# 共通エンジンを参照
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '共通', 'scripts'))
from generate_testcases import BugakariJSON

BASE = os.path.join(os.path.dirname(__file__), '..')
INPUT = os.path.join(BASE, 'input')
OUTPUT = os.path.join(BASE, 'output')

JSON_NEW = os.path.join(INPUT, '32-1951.20260401.20260401.json')

bugakari = BugakariJSON(JSON_NEW)

# BoxNo→UI表示名の解決
name_106 = bugakari.resolve_boxno_name(106)   # 損料・油圧式・排対2014年規制
name_124 = bugakari.resolve_boxno_name(124)   # 運転日当り運転時間適用区分

HEADER = [
    'テストID',
    'テスト区分',
    '積算方法の選択',
    '単価表の単位選択',
    '排ガス規格選択',
    '期待：損料計上',
    '期待：フロー遷移先',
    '確認観点',
]

rows = [HEADER]

# TC-001: 差分 — 新規排ガス規格で正しい損料が計上されるか
rows.append([
    'TC-001',
    '差分',
    '任意',
    '任意',
    name_106,
    '(排対2014年規制)油圧式1300kg級 ベースマシン20t級',
    '-',
    f'「{name_106}」を選択したとき、対応する損料が代価表に計上されること',
])

# TC-002: 差分 — 新規排ガス規格を選択後、フローが正しく遷移するか
callbox_names = bugakari.resolve_callbox_names(106)
callbox_display = callbox_names[0] if callbox_names else name_124

rows.append([
    'TC-002',
    '差分',
    '任意',
    '任意',
    name_106,
    '-',
    callbox_display,
    f'「{name_106}」を選択後、「{callbox_display}」へ遷移すること（フロー遷移の確認）',
])

# TC-003: 回帰 — 既存の排ガス規格選択に変化がないか
rows.append([
    'TC-003',
    '回帰',
    '任意',
    '任意',
    '既存の排ガス規格（2014年規制以外）',
    '旧版と同じ損料',
    '-',
    '旧バージョンで選択できた排ガス規格（排対1次・2次・3次）を選択したとき、計上内容が旧版と変わっていないこと',
])

os.makedirs(OUTPUT, exist_ok=True)
out_path = os.path.join(OUTPUT, 'テストケース仕様書_32-1951_大型ブレーカ_2026.csv')
BugakariJSON.write_csv(rows, out_path)
print(f'生成完了: {out_path}')
print(f'  BoxNo:106 → {name_106}')
print(f'  BoxNo:124 → {name_124}')
print(f'  TC-002 フロー遷移先: {callbox_display}')
