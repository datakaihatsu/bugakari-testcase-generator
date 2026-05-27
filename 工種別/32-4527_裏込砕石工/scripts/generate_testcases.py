import csv
import io

# 2026年パラメータ
D = 40.0          # 日当り施工量（2026）
COEFF = 1.3       # 砕石係数（2026）

# BH施工 歩掛値（2026）
BH = {'S1A': 0.8, 'S2A': 1.6, 'S3A': 3.6, 'S5A': 1.0}

# クレーン施工 歩掛値（2025/2026共通、つき固有）
CRANE = {'S1A': 0.7, 'S2A': 1.3, 'S3A': 3.3, 'S5A': 1.0}

materials = [
    ('材料費+施工費', 1),
    ('施工費のみ',    2),
]

# 排ガス機械: 2026年新規追加機能のため両選択肢を展開
# 選択肢テキストの適切さはJSONから語れないため「問いかけ」列で確認
exhausts = ['2014年規制', '第3次基準値']

# 排ガス機械ごとの確認内容（変更があった箇所のみ問いかける）
# セル内改行で箇条書き形式にする
EXHAUST_CHECK = {
    '2014年規制': (
        '排ガス機械（2026年新規追加）\n'
        '・「排ガス対策型（2014年規制）」と表示されているが、外部設計と正しいか'
    ),
    '第3次基準値': (
        '排ガス機械（2026年新規追加）\n'
        '・「排ガス対策型（第3次基準値）」と表示されているが、外部設計と正しいか'
    ),
}

HEADERS = [
    'テストID', 'テスト区分',
    '資材計上区分', '砕石の種類', '排ガス機械', '代価表当り単位（固定）', '労務費の適用',
    '期待：S1', '期待：S2', '期待：S3', '期待：S4', '期待：S5',
    '選択肢の適切さ確認',
]

def fmt(v):
    if v is None:
        return ''
    r = round(v, 4)
    s = f'{r:.4f}'.rstrip('0')
    if s.endswith('.'):
        s += '0'
    return s

def make_row(tc, test_type, mat_name, unit_name, exhaust, A, p,
             sekisui='再生クラッシャラン40~0', roumu='普通作業員'):
    AT = D if A == 0 else float(A)
    LD = AT / D
    S1 = p['S1A'] * LD
    S2 = p['S2A'] * LD
    S3 = p['S3A'] * LD
    S4 = AT * COEFF if mat_name == '材料費+施工費' else None
    S5 = p['S5A'] * LD
    check = EXHAUST_CHECK.get(exhaust, '')
    # 列順: 資材計上区分→砕石の種類→排ガス機械→代価表当り単位（固定）→労務費の適用→期待値→確認
    return [
        f'TC-{tc:03d}', test_type,
        mat_name, sekisui, exhaust, unit_name, roumu,
        fmt(S1), fmt(S2), fmt(S3), fmt(S4), fmt(S5),
        check,
    ]

all_rows = []
tc = 1

# フェーズ1: 必須テスト（SF=2.1固定 / 治山林道×BH施工のみ動作）
# 代価表当り単位: 10m3固定（SFK1a=1 / ユーザー選択画面が開かない）
# 資材計上区分 × 排ガス機械: 2×2=4件
all_rows.append(HEADERS)
for mat_name, sk in materials:
    for exhaust in exhausts:
        all_rows.append(make_row(tc, '差分', mat_name, '10m3', exhaust, 10, BH))
        tc += 1

# CSV文字列生成
buf = io.StringIO()
writer = csv.writer(buf, lineterminator='\r\n')
for row in all_rows:
    writer.writerow(row)
csv_text = buf.getvalue()

# Shift-JIS（cp932）で保存
out_path = r'C:\Users\imoo\Desktop\ClaudeCode\14.歩掛Jsonからテストケース作成可能か\工種別\32-4527_裏込砕石工\output\テストケース仕様書_32-4527_裏込砕石工_2026.csv'
with open(out_path, 'w', encoding='cp932', newline='') as f:
    f.write(csv_text)

print(f'生成完了: {tc - 1}件')
print()
print(csv_text)
