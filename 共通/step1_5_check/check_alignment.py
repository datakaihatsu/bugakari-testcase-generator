"""
①.5 修正方針との乖離チェック (精度向上版)

input:  修正方針.txt + step1_差分レポート.csv
output: step1.5_乖離チェック.csv

【役割】
業務担当者が書いた「修正方針.txt」と、自動検出された「step1_差分レポート.csv」を
照合し、検出漏れ・過剰検出を把握する。緩和一致率の目標は 75〜80%。

【一致率の計算】
- 分母: 修正方針.txt から抽出した「主項目」数
- 分子: 各主項目に対応する step1 行が見つかった件数

【精度向上策 (2026-05-28)】
A. 【質問表】形式のカテゴリヘッダを認識
B. 親子インデント構造を集約 (子項目は親に併合し主項目1件として扱う)
C. 日本語キーワード(2文字以上)を抽出して照合
D. カテゴリ一致のみでも partial として扱う
"""

import sys
import os
import csv
import re

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'engine'))
from bugakari_json import BugakariJSON


# カテゴリ標準化
CATEGORY_KEYWORDS = {
    '代価表': '代価表',
    '計算表': '計算表',
    '質問表': '質問',
    '質問': '質問',
    '質問設定': '質問設定',
    '選択肢': '選択肢',
    'フロー': 'フロー',
}

# 行頭マーカー (Markdown/Textile/Redmine)
MARKER_RE = re.compile(r'^([\s　]*)([+\-*●・◯○■◆★☆※#]+|h\d\.|\d+[\.\)、])')

# 装飾 (Textile/Redmine 風) — 削除する装飾。【】は別扱い(カテゴリ抽出用)
DECORATION_RE = re.compile(r'(\+<[^>]+>\+|\[[^\]]+\]|%\{[^}]+\}[^%]*%)')

# 【...】 カテゴリ抽出
KAKKO_CATEGORY_RE = re.compile(r'【([^】]+)】')

# 日本語キーワード(2文字以上のひらがな/カタカナ/漢字連続)
JAPANESE_KW_RE = re.compile(r'[一-龯ぁ-んァ-ヶー]{2,}')

# 修正方針タイトル候補
TITLE_PATTERNS = ('修正方針', '修正内容', '変更内容')


HEADER = ['修正方針カテゴリ', '修正方針本文', '状態', 'マッチしたstep1行', 'マッチstep1 ID']


def _detect_category(text):
    """テキスト内に CATEGORY_KEYWORDS のいずれかが含まれていれば標準カテゴリを返す"""
    for kw, std in CATEGORY_KEYWORDS.items():
        if kw in text:
            return std
    return None


def _indent_level(raw_line):
    """行頭インデントレベルを判定。マーカーの個数で判定。
    `*` = 1, `**` = 2, `-` = 1, `・` = 1, ' ' 4個 = 1 など。
    """
    m = MARKER_RE.match(raw_line)
    if m:
        marker = m.group(2)
        if marker.startswith('*'):
            return marker.count('*')
        if marker.startswith('-') or marker == '・' or marker in '●◯○':
            return 1
        return 1
    # スペース・タブインデント
    leading = raw_line[:len(raw_line) - len(raw_line.lstrip(' 　\t'))]
    return (len(leading.replace('\t', '    ')) // 4) + 0


def load_intent(path):
    """修正方針.txt を読み、主項目リストを返す。

    精度向上策:
      A. 【...】を先にカテゴリとして抽出
      B. 親子インデント構造を集約: 子項目(深いインデント)は直前の親に併合
    """
    try:
        with open(path, encoding='utf-8-sig') as f:
            text = f.read()
    except UnicodeDecodeError:
        with open(path, encoding='cp932') as f:
            text = f.read()

    items = []
    current_category = None
    current_item = None      # 親項目(主項目)を蓄積
    current_indent = 0

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        # A. 【...】 からカテゴリ抽出 (削除より先に)
        m_kakko = KAKKO_CATEGORY_RE.search(line)
        if m_kakko:
            cat = _detect_category(m_kakko.group(1))
            if cat:
                current_category = cat
                # 当該行が完全にカテゴリヘッダのみなら item化しない
                rest = KAKKO_CATEGORY_RE.sub('', line).strip()
                rest = MARKER_RE.sub('', rest).strip()
                if not rest:
                    # 主項目進行中なら confirm
                    if current_item is not None:
                        items.append(current_item)
                        current_item = None
                    continue

        # マーカー・装飾を除去
        indent_lv = _indent_level(raw_line)
        body = MARKER_RE.sub('', line).strip()
        body = DECORATION_RE.sub('', body).strip()
        # 【...】 を 本文からも除去 (カテゴリ抽出用に残してた)
        body = KAKKO_CATEGORY_RE.sub('', body).strip()
        if not body:
            continue

        # タイトル行(■修正方針)はスキップ
        if any(p in body for p in TITLE_PATTERNS) and len(body) <= 12:
            continue

        # カテゴリヘッダ判定 (本文だけで短い)
        if len(body) <= 8:
            cat = _detect_category(body)
            if cat:
                current_category = cat
                if current_item is not None:
                    items.append(current_item)
                    current_item = None
                continue

        # B. 親子集約: インデントが深ければ前の主項目に追記
        if current_item is not None and indent_lv > current_indent:
            current_item['本文'] += ' / ' + body
            current_item['追加情報'].append(body)
            continue

        # 新しい主項目
        if current_item is not None:
            items.append(current_item)
        current_item = {
            'カテゴリ': current_category or '?',
            '本文': body,
            '追加情報': [],
            '元行': raw_line,
        }
        current_indent = indent_lv

    if current_item is not None:
        items.append(current_item)

    return items


def extract_keywords(body):
    """項目本文から照合用キーワードを抽出。
    - 英数字記号の識別子 (S4, FG1, FG~01 等)
    - 質問No
    - 数値
    - 動詞
    - 日本語キーワード(2文字以上)
    """
    kws = set()
    # 英数字識別子
    for m in re.finditer(r'[A-Za-z][\w~\']*', body):
        if len(m.group()) >= 2:
            kws.add(m.group())
        elif m.group().lower() in ('s', 'd'):
            # S1, S4 等の "S" 単体は短いがあえて拾わない (誤マッチ多発)
            pass
    # 質問No
    for m in re.finditer(r'質問\s*No\s*\.?\s*[:：]?\s*(\d+)', body):
        kws.add(f'質問No:{m.group(1)}')
    # 数値
    for m in re.finditer(r'\d+(?:\.\d+)?', body):
        if len(m.group()) >= 2:
            kws.add(m.group())
    # 動詞
    for verb in ('追加', '削除', '変更', '持ってくる', '複写', '新規', '作成', '配置', '反映'):
        if verb in body:
            kws.add(verb)
    # C. 日本語キーワード(2文字以上)
    for m in JAPANESE_KW_RE.finditer(body):
        kw = m.group()
        # 動詞・カテゴリ語は除外(他で扱う)
        if kw in ('変更', '追加', '削除', '質問', '計算表', '代価表', '質問表', '選択肢', 'フロー'):
            continue
        if len(kw) >= 2:
            kws.add(kw)
    return kws


def load_diff(path):
    with open(path, encoding='cp932', newline='') as f:
        return list(csv.DictReader(f))


def match_item(item, diff_rows, used_indices):
    """項目 item に対応する step1 行を探す。
    戻り値: (best_index or None, matched_score)
    score:
      0 = no match
      1 = カテゴリ一致のみ (or 弱キーワード1個)
      2 以上 = キーワード複数一致 = 完全マッチ
    """
    body = item['本文']
    kws = extract_keywords(body)
    cat = item['カテゴリ']

    best_idx = None
    best_score = 0
    for idx, row in enumerate(diff_rows):
        if idx in used_indices:
            continue
        cat_match = (cat == '?' or row.get('カテゴリ') == cat)
        if not cat_match and cat != '?':
            continue
        # キーワード照合
        text_in_row = ' '.join([
            row.get('ID', ''), row.get('名称', ''),
            row.get('旧値', ''), row.get('新値', ''),
            row.get('変更種別', ''), row.get('備考', ''),
        ])
        score = 0
        for kw in kws:
            if kw and kw in text_in_row:
                score += 1
        # D. カテゴリ一致のみでも partial として扱う
        if score == 0 and cat_match and cat != '?':
            score = 0
        if score > best_score:
            best_score = score
            best_idx = idx
    return best_idx, best_score


def run(intent_path, diff_csv_path, output_path):
    if not os.path.exists(intent_path):
        print(f'警告: 修正方針ファイルが存在しません: {intent_path}')
        BugakariJSON.write_csv(
            [HEADER, ['', '修正方針.txt が存在しないため照合できず', 'no_intent', '', '']],
            output_path,
        )
        return 0.0, 0, 0

    items = load_intent(intent_path)
    diff_rows = load_diff(diff_csv_path)

    result_rows = []
    used_indices = set()
    matched_count = 0
    partial_count = 0
    diff_categories = set(row.get('カテゴリ', '') for row in diff_rows)

    for item in items:
        idx, score = match_item(item, diff_rows, used_indices)
        if score >= 2:
            used_indices.add(idx)
            row = diff_rows[idx]
            matched_count += 1
            result_rows.append([
                item['カテゴリ'],
                item['本文'][:120],
                'matched',
                f"{row.get('カテゴリ','')}/{row.get('変更種別','')}/{row.get('名称','')}",
                row.get('ID', ''),
            ])
        elif score == 1 and idx is not None:
            used_indices.add(idx)
            row = diff_rows[idx]
            partial_count += 1
            result_rows.append([
                item['カテゴリ'],
                item['本文'][:120],
                'partial',
                f"{row.get('カテゴリ','')}/{row.get('変更種別','')}/{row.get('名称','')}",
                row.get('ID', ''),
            ])
        else:
            if item['カテゴリ'] != '?' and item['カテゴリ'] in diff_categories:
                partial_count += 1
                result_rows.append([
                    item['カテゴリ'],
                    item['本文'][:120],
                    'category_partial',
                    '(同カテゴリの差分あり)',
                    '',
                ])
            else:
                result_rows.append([
                    item['カテゴリ'],
                    item['本文'][:120],
                    'unmatched',
                    '',
                    '',
                ])

    diff_only_count = 0
    for idx, row in enumerate(diff_rows):
        if idx in used_indices:
            continue
        diff_only_count += 1
        result_rows.append([
            row.get('カテゴリ', ''),
            f'(step1側のみ) {row.get("名称","")}'[:120],
            'diff_only',
            f"{row.get('カテゴリ','')}/{row.get('変更種別','')}/{row.get('名称','')}",
            row.get('ID', ''),
        ])

    total_intent = len(items)
    rate_strict = matched_count / total_intent if total_intent else 0
    rate_loose = (matched_count + partial_count) / total_intent if total_intent else 0

    summary_rows = [
        ['---', '---', '---', '---', '---'],
        ['【サマリー】', f'修正方針項目: {total_intent}件',
         f'完全マッチ: {matched_count}', f'部分マッチ: {partial_count}',
         f'未マッチ: {total_intent - matched_count - partial_count}'],
        ['', f'一致率(厳密): {rate_strict:.0%}',
         f'一致率(緩和): {rate_loose:.0%}',
         f'step1のみ(過剰検出候補): {diff_only_count}件', ''],
    ]

    BugakariJSON.write_csv([HEADER] + result_rows + summary_rows, output_path)

    print(f'乖離チェック完了: {output_path}')
    print(f'  修正方針項目数: {total_intent}')
    print(f'  完全マッチ: {matched_count} / 部分マッチ: {partial_count} / 未マッチ: {total_intent - matched_count - partial_count}')
    print(f'  一致率(厳密 score>=2): {rate_strict:.0%}')
    print(f'  一致率(緩和 score>=1 or cat-only): {rate_loose:.0%}')
    print(f'  step1側のみ(過剰検出候補): {diff_only_count}件')
    return rate_loose, matched_count + partial_count, total_intent


if __name__ == '__main__':
    if len(sys.argv) < 4:
        print('Usage: python check_alignment.py <intent_txt> <step1_diff_csv> <output_csv>')
        sys.exit(1)
    run(sys.argv[1], sys.argv[2], sys.argv[3])
