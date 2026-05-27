"""
03_607674 送気用設備運転費 テストケース生成
32-571 / 2025→2026 改定対応

仕様: ../spec/context_確定仕様.md
"""

import sys
import os

# 共通エンジンを参照
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '共通', 'scripts'))
from generate_testcases import BugakariJSON, fmt

BASE = os.path.join(os.path.dirname(__file__), '..')
INPUT = os.path.join(BASE, 'input')
OUTPUT = os.path.join(BASE, 'output')

JSON_NEW = os.path.join(INPUT, '32-571.20260401.20260401.json')

bugakari = BugakariJSON(JSON_NEW)

# 固定係数の取得（JSONから実測）
S1A = bugakari.get_keisan_value('S1A') or 1.0   # 特殊作業員係数（固定1.0）
S2A = bugakari.get_keisan_value('S2A') or 0.3   # 電工係数（固定0.3）

def s1(ut):
    return S1A * 3 * ut

def s2(ut):
    return S2A * ut

HEADER = [
    'テストID',
    'テスト区分',
    '運転日数',
    'クーリングタワー使用の有無',
    '空気圧縮機規格選択',
    '空気圧縮機延運転時間(h)',
    '期待：特殊作業員(S1)',
    '期待：電工(S2)',
    '期待：代価表行数',
    '期待：電力料S3(kWh)',
    '確認観点',
]

rows = [HEADER]

# TC-001: 差分 — 運転日数の質問が表示され、代価表が6行になっているか
rows.append([
    'TC-001',
    '差分',
    '任意',
    '任意',
    '任意',
    '任意',
    '-',
    '-',
    '6行（旧3行→新6行）',
    '-',
    '「運転日数」の入力欄が表示されること、代価表の行数が旧版の3行から6行に増えていること',
])

# TC-002: 差分 — UT=1 のとき S1=3.000, S2=0.300 が計上されるか
ut2 = 1
rows.append([
    'TC-002',
    '差分',
    str(ut2),
    '任意',
    '任意',
    '任意',
    fmt(s1(ut2)),
    fmt(s2(ut2)),
    '6行',
    '-',
    f'運転日数={ut2}日のとき、特殊作業員={fmt(s1(ut2))}人・電工={fmt(s2(ut2))}人が計上されること',
])

# TC-003: 差分 — UT=5 のとき S1=15.000, S2=1.500 が計上されるか（スケール確認）
ut3 = 5
rows.append([
    'TC-003',
    '差分',
    str(ut3),
    '任意',
    '任意',
    '任意',
    fmt(s1(ut3)),
    fmt(s2(ut3)),
    '6行',
    '-',
    f'運転日数={ut3}日のとき、特殊作業員={fmt(s1(ut3))}人・電工={fmt(s2(ut3))}人が計上されること（UT増加による比例スケール確認）',
])

# TC-004: 回帰 — 旧版の電力料（S3=8600kWh）が変わっていないか
# 空気圧縮機規格: 低圧定置式スクリュー型 29.0m3/min・50Hz（S3A=86）, 延運転時間=100h
rows.append([
    'TC-004',
    '回帰',
    '任意',
    '任意',
    '低圧定置式スクリュー型 29.0m3/min・50Hz',
    '100',
    '-',
    '-',
    '-',
    '8600',
    '上記の規格・時間を入力したとき、電力料（空気圧縮機）が8600kWhであること（旧版から変化がないこと）',
])

os.makedirs(OUTPUT, exist_ok=True)
out_path = os.path.join(OUTPUT, 'テストケース仕様書_32-571_送気用設備運転費_2026.csv')
BugakariJSON.write_csv(rows, out_path)
print(f'生成完了: {out_path}')
print(f'  S1A={S1A}, S2A={S2A}')
print(f'  TC-002: UT=1 → S1={fmt(s1(1))}, S2={fmt(s2(1))}')
print(f'  TC-003: UT=5 → S1={fmt(s1(5))}, S2={fmt(s2(5))}')
