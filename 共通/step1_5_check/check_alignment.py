"""
①.5 修正方針との乖離チェック (リスク評価対応版)

input:  修正方針.txt + step1.0_差分レポート.csv (+ オプション: claude_risk_judgement.yaml)
output: step1.5_乖離チェック.csv

【役割】
業務担当者が書いた「修正方針.txt」と、自動検出された「step1.0_差分レポート.csv」を
照合し、検出漏れ・過剰検出を把握する。

【一致率の計算】
- 分母: 修正方針.txt から抽出した「主項目」数
- 分子: 各主項目に対応する step1 行が見つかった件数

【精度向上策 (2026-05-28)】
A. 【質問表】形式のカテゴリヘッダを認識
B. 親子インデント構造を集約 (子項目は親に併合し主項目1件として扱う)
C. 日本語キーワード(2文字以上)を抽出して照合
D. カテゴリ一致のみでも partial として扱う

【リスク評価対応 (2026-05-28追加)】
機械評価で 70〜80% 一致 → 残り 20〜30% を Claude (暫定運用)が業務知見でリスク評価。
工種ごとの判定結果は `<output>/claude_risk_judgement.yaml` に蓄積。

> 重要: Claude判定は暫定運用。将来は静的ルール化・業務担当者運用に置き換える。
> 詳細: 共通/spec/汎用テストケース生成仕様.md「Claude判定の暫定運用と将来の置き換え戦略」
"""

import sys
import os
import csv
import re

try:
    import yaml
except ImportError:
    yaml = None

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

# 行頭マーカー
MARKER_RE = re.compile(r'^([\s　]*)([+\-*●・◯○■◆★☆※#]+|h\d\.|\d+[\.\)、])')

# 装飾
DECORATION_RE = re.compile(r'(\+<[^>]+>\+|\[[^\]]+\]|%\{[^}]+\}[^%]*%)')

# 【...】 カテゴリ抽出
KAKKO_CATEGORY_RE = re.compile(r'【([^】]+)】')

# 日本語キーワード
JAPANESE_KW_RE = re.compile(r'[一-龯ぁ-んァ-ヶー]{2,}')

# 修正方針タイトル候補
TITLE_PATTERNS = ('修正方針', '修正内容', '変更内容')

# 出力列 (リスク評価3列追加)
HEADER = [
    '修正方針カテゴリ', '修正方針本文', '機械判定',
    'マッチstep1行', 'マッチstep1 ID',
    'Claudeリスク評価', 'Claudeコメント', '最終判定',
]

# Claudeリスク評価 → 最終判定マッピング
RISK_TO_FINAL = {
    '妥当': 'OK',
    '軽微': 'OK',
    '重大': '要対応',
}


def _detect_category(text):
    for kw, std in CATEGORY_KEYWORDS.items():
        if kw in text:
            return std
    return None


def _indent_level(raw_line):
    m = MARKER_RE.match(raw_line)
    if m:
        marker = m.group(2)
        if marker.startswith('*'):
            return marker.count('*')
        if marker.startswith('-') or marker == '・' or marker in '●◯○':
            return 1
        return 1
    leading = raw_line[:len(raw_line) - len(raw_line.lstrip(' 　\t'))]
    return (len(leading.replace('\t', '    ')) // 4) + 0


def load_intent(path):
    """修正方針.txt を読み、主項目リストを返す"""
    try:
        with open(path, encoding='utf-8-sig') as f:
            text = f.read()
    except UnicodeDecodeError:
        with open(path, encoding='cp932') as f:
            text = f.read()

    items = []
    current_category = None
    current_item = None
    current_indent = 0

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        m_kakko = KAKKO_CATEGORY_RE.search(line)
        if m_kakko:
            cat = _detect_category(m_kakko.group(1))
            if cat:
                current_category = cat
                rest = KAKKO_CATEGORY_RE.sub('', line).strip()
                rest = MARKER_RE.sub('', rest).strip()
                if not rest:
                    if current_item is not None:
                        items.append(current_item)
                        current_item = None
                    continue

        indent_lv = _indent_level(raw_line)
        body = MARKER_RE.sub('', line).strip()
        body = DECORATION_RE.sub('', body).strip()
        body = KAKKO_CATEGORY_RE.sub('', body).strip()
        if not body:
            continue

        if any(p in body for p in TITLE_PATTERNS) and len(body) <= 12:
            continue

        if len(body) <= 8:
            cat = _detect_category(body)
            if cat:
                current_category = cat
                if current_item is not None:
                    items.append(current_item)
                    current_item = None
                continue

        if current_item is not None and indent_lv > current_indent:
            current_item['本文'] += ' / ' + body
            current_item['追加情報'].append(body)
            continue

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
    kws = set()
    for m in re.finditer(r'[A-Za-z][\w~\']*', body):
        if len(m.group()) >= 2:
            kws.add(m.group())
    for m in re.finditer(r'質問\s*No\s*\.?\s*[:：]?\s*(\d+)', body):
        kws.add(f'質問No:{m.group(1)}')
    for m in re.finditer(r'\d+(?:\.\d+)?', body):
        if len(m.group()) >= 2:
            kws.add(m.group())
    for verb in ('追加', '削除', '変更', '持ってくる', '複写', '新規', '作成', '配置', '反映'):
        if verb in body:
            kws.add(verb)
    for m in JAPANESE_KW_RE.finditer(body):
        kw = m.group()
        if kw in ('変更', '追加', '削除', '質問', '計算表', '代価表', '質問表', '選択肢', 'フロー'):
            continue
        if len(kw) >= 2:
            kws.add(kw)
    return kws


def load_diff(path):
    with open(path, encoding='cp932', newline='') as f:
        return list(csv.DictReader(f))


def load_risk_judgement(path):
    """claude_risk_judgement.yaml を読み込む。

    形式:
      version: 1
      judged_at: YYYY-MM-DD
      judged_by: claude (assistant)
      judgements:
        - intent_body: "修正方針本文の前方一致または完全一致"
          risk: 妥当 / 軽微 / 重大
          comment: 理由
    """
    if yaml is None or not os.path.exists(path):
        return []
    with open(path, encoding='utf-8') as f:
        data = yaml.safe_load(f) or {}
    return data.get('judgements') or []


def load_overdetection_judgements(path):
    """claude_risk_judgement.yaml の overdetection_judgements を読む。

    形式:
      overdetection_judgements:
        - target: "カテゴリ/変更種別/名称" の前方一致
                  (例 '質問/変更/' で一括パターン / '質問/変更/省庁区分' で個別)
          risk: 妥当 / 軽微 / 重大
          comment: 理由
    JIT: 同種変更の再来で再利用でき、target を粗くすれば「パターン判定=機械ルール昇格」
         の足場になる (前処理と解釈の境界を動かせる)。
    """
    if yaml is None or not os.path.exists(path):
        return []
    with open(path, encoding='utf-8') as f:
        data = yaml.safe_load(f) or {}
    return data.get('overdetection_judgements') or []


def find_judgement(item_body, judgements):
    """item_body にマッチする判定を返す"""
    for j in judgements:
        intent_body = j.get('intent_body', '')
        if not intent_body:
            continue
        # 前方一致 or 完全一致
        if intent_body in item_body or item_body.startswith(intent_body):
            return j
    return None


def match_item(item, diff_rows, used_indices):
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
        text_in_row = ' '.join([
            row.get('ID', ''), row.get('名称', ''),
            row.get('旧値', ''), row.get('新値', ''),
            row.get('変更種別', ''), row.get('備考', ''),
        ])
        score = 0
        for kw in kws:
            if kw and kw in text_in_row:
                score += 1
        if score > best_score:
            best_score = score
            best_idx = idx
    return best_idx, best_score


def run(intent_path, diff_csv_path, output_path, new_json_path=None, old_json_path=None):
    if not os.path.exists(intent_path):
        print(f'警告: 修正方針ファイルが存在しません: {intent_path}')
        BugakariJSON.write_csv(
            [HEADER, ['', '修正方針.txt が存在しないため照合できず', 'no_intent', '', '', '', '', '']],
            output_path,
        )
        return 0.0, 0, 0

    items = load_intent(intent_path)
    diff_rows = load_diff(diff_csv_path)

    # 番号ずれノイズ判定用: 新旧JSONの 質問名(Mesho) と 代価表TankaCD の集合。
    #   step1 は SitsumonNo で突合する(正しい)。空練りモルタル削除等で番号がずれると
    #   同じ質問が別Noになり「変更/追加/削除」として大量に出る。名前が新旧両方に在れば
    #   実体は移動しただけ=番号ずれ → ノイズ。step1 は一切変更しない。
    import json as _json
    def _load_names(p):
        if not p or not os.path.exists(p):
            return set(), set()
        with open(p, encoding='utf-8-sig') as f:
            d = _json.load(f)
        qn = set((it.get('Mesho') or '') for it in d.get('SitsumonItem', []) if it.get('Mesho'))
        tk = set((di.get('TankaCD') or '') for di in d.get('DaikaItem', []) if di.get('TankaCD'))
        return qn, tk
    new_qnames, new_tanka = _load_names(new_json_path)
    old_qnames, old_tanka = _load_names(old_json_path)

    # Claude判定を読み込み (output ディレクトリの claude_risk_judgement.yaml)
    output_dir = os.path.dirname(output_path)
    judgement_path = os.path.join(output_dir, 'claude_risk_judgement.yaml')
    judgements = load_risk_judgement(judgement_path)

    result_rows = []
    used_indices = set()
    matched_count = 0
    partial_count = 0
    diff_categories = set(row.get('カテゴリ', '') for row in diff_rows)

    # Claudeリスク評価カウント
    risk_appropriate = 0  # 妥当
    risk_minor = 0        # 軽微
    risk_critical = 0     # 重大
    risk_unjudged = 0     # 未評価 (unmatched で Claude判定なし)

    for item in items:
        idx, score = match_item(item, diff_rows, used_indices)

        # 機械判定
        if score >= 2:
            used_indices.add(idx)
            row = diff_rows[idx]
            mech_status = 'matched'
            matched_count += 1
            match_str = f"{row.get('カテゴリ','')}/{row.get('変更種別','')}/{row.get('名称','')}"
            match_id = row.get('ID', '')
        elif score == 1 and idx is not None:
            used_indices.add(idx)
            row = diff_rows[idx]
            mech_status = 'partial'
            partial_count += 1
            match_str = f"{row.get('カテゴリ','')}/{row.get('変更種別','')}/{row.get('名称','')}"
            match_id = row.get('ID', '')
        elif item['カテゴリ'] != '?' and item['カテゴリ'] in diff_categories:
            mech_status = 'category_partial'
            partial_count += 1
            match_str = '(同カテゴリの差分あり)'
            match_id = ''
        else:
            mech_status = 'unmatched'
            match_str = ''
            match_id = ''

        # Claudeリスク評価適用
        claude_risk = ''
        claude_comment = ''
        if mech_status in ('matched', 'partial', 'category_partial'):
            final_status = 'OK'
        else:
            # unmatched: Claude判定を探す
            j = find_judgement(item['本文'], judgements)
            if j:
                claude_risk = j.get('risk', '')
                claude_comment = j.get('comment', '')
                final_status = RISK_TO_FINAL.get(claude_risk, '未評価')
                if claude_risk == '妥当':
                    risk_appropriate += 1
                elif claude_risk == '軽微':
                    risk_minor += 1
                elif claude_risk == '重大':
                    risk_critical += 1
            else:
                final_status = '未評価'
                risk_unjudged += 1

        result_rows.append([
            item['カテゴリ'],
            item['本文'][:120],
            mech_status,
            match_str,
            match_id,
            claude_risk,
            claude_comment[:80] if claude_comment else '',
            final_status,
        ])

    # step1側のみ (過剰検出候補) — 仕様(修正方針)に無い実装差分。
    #   JIT 運用では「実装が仕様より多くやっている」可能性を拾う双方向ゲートとして
    #   構造変化(要確認)とノイズ(計算表等の内部再計算)に分類する。
    #   - 要確認: 質問/選択肢/代価表/フロー/質問設定 の追加・削除・変更
    #             = リリース済み商品なら仕様に出るべき構造変化。仕様に無ければバグ疑い。
    #   - ノイズ: 計算表(内部変数の式変更・振り直し・派生再計算) 等 = 仕様に出なくて当然。
    STRUCTURAL_CATEGORIES = ('質問', '選択肢', '代価表', 'フロー', '質問設定')
    # ターゲット単位カバレッジ: 修正方針が「対象」に言及していれば、その対象に対する
    #   step1 差分は意図カバー済みとみなす (粒度ノイズ対策)。
    #   - covered_ids : マッチした差分の ID (例 質問No:3, BoxNo:3) → 同一対象の兄弟差分もカバー
    #   - intent_text : 修正方針本文。差分名称の核 (括弧書き除去) が含まれればカバー
    import re as _re
    covered_ids = set()
    for i in used_indices:
        cid = diff_rows[i].get('ID')
        if cid:
            covered_ids.add(cid)
    intent_text = ' '.join((it.get('本文', '') or '') for it in items)

    def _core_name(nm):
        # 括弧書き(全角/半角)を除いた核を返す
        nm = _re.sub(r'[\(（][^)）]*[\)）]', '', nm or '')
        return nm.strip()

    def _is_covered(row):
        rid = row.get('ID', '')
        if rid and rid in covered_ids:
            return True
        core = _core_name(row.get('名称', ''))
        if core and len(core) >= 2 and core in intent_text:
            return True
        return False

    def _is_internal_name(nm):
        """名称が内部行か。
        - 「変数=値」(= の左辺が非日本語: K4W=1, S5A=5.3, FG1a=31 等)
        - 代価表行フラグ「N=代価表X枚目Y行目」
        これらは内部の自動選択/計算行で、ユーザ向け構造変化ではない → ノイズ。
        generate_proposals._is_ui_visible_axis と同じ判定基準。
        """
        nm = nm or ''
        # フロー終端マーカー「終点」はフロー編集の構造的副作用で仕様非関連 → ノイズ。
        if nm.strip() == '終点':
            return True
        # 代価表行マッピング「代価表X枚目Y行目=VAR」「N=代価表X枚目Y行目」は内部行 → ノイズ。
        if '代価表' in nm and '枚目' in nm and '行目' in nm:
            return True
        if '=' in nm:
            left = nm.split('=', 1)[0]
            if not any(ord(c) >= 128 for c in left):
                return True
        return False

    def _is_version_noise(change_kind, biko):
        """変更種別=変更 で、備考が「バージョン更新／表示名変更」のみのものはノイズ。
        年次版更新の副作用で構造変化でない。『備考変更』(誤字修正等)は除外して残す。
        """
        if change_kind != '変更':
            return False
        b = (biko or '')
        if not b:
            return False
        for t in ('表示名変更', 'バージョン更新'):
            b = b.replace(t, '')
        b = b.replace('/', '').replace(' ', '').replace('　', '').strip()
        return b == ''

    def _is_renumber_noise(row):
        """番号ずれの波及か。step1がSitsumonNoで突合するため、版更新で番号がずれた
        質問/選択肢/フロー/代価表が変更・追加・削除として出る。名前(質問Mesho/TankaCD)が
        新旧両方に在る = 実体は移動しただけ → ノイズ。
        - 追加: その名前が旧にも在る → 既存(=番号ずれ)
        - 削除: その名前が新にも在る → まだ在る(=番号ずれ)
        - 変更: その名前(新名/旧名)が新旧両方に在る → 入れ替わり(=番号ずれ)
        """
        cat = row.get('カテゴリ', '')
        kind = row.get('変更種別', '')
        nm = (row.get('名称') or '').strip()
        old_v = (row.get('旧値') or '').strip()
        if cat == '代価表':
            qold, qnew = old_tanka, new_tanka
            key = nm.replace('TankaCD:', '') if nm.startswith('TankaCD:') else nm
            if not key:
                return False
            if kind == '追加':
                return key in qold
            if kind == '削除':
                return key in qnew
            if kind == '変更':
                return key in qold and key in qnew
            return False
        qold, qnew = old_qnames, new_qnames
        if not (qold or qnew):
            return False  # JSON未指定なら無効
        if not nm:
            return False
        if kind == '追加':
            return nm in qold
        if kind == '削除':
            return nm in qnew
        if kind == '変更':
            if nm in qold and nm in qnew:
                return True
            if old_v and old_v in qold and old_v in qnew:
                return True
        return False

    over_judgements = load_overdetection_judgements(judgement_path)

    def _find_over_judgement(sig):
        # target が署名の前方一致。最長(=最も具体的)を採用。
        best, best_len = None, -1
        for j in over_judgements:
            t = j.get('target', '')
            if t and sig.startswith(t) and len(t) > best_len:
                best, best_len = j, len(t)
        return best

    diff_only_count = 0
    review_needed = 0     # 構造変化の残差(=要確認候補)合計
    over_noise = 0
    over_covered = 0
    over_appropriate = 0  # 過剰だが妥当 (Claude解釈)
    over_minor = 0        # 過剰・軽微
    over_critical = 0     # 過剰・重大 (真のバグ疑い)
    over_unjudged = 0     # 要確認だが Claude 未解釈 (JIT 解釈トリガー)
    for idx, row in enumerate(diff_rows):
        if idx in used_indices:
            continue
        diff_only_count += 1
        cat = row.get('カテゴリ', '')
        over_risk = ''
        over_comment = ''
        if cat not in STRUCTURAL_CATEGORIES:
            over_class = 'ノイズ'; over_noise += 1
        elif _is_internal_name(row.get('名称', '')):
            # 内部行(変数=値, 代価表行フラグ, 終点) → ノイズ。
            over_class = 'ノイズ'; over_noise += 1
        elif _is_version_noise(row.get('変更種別', ''), row.get('備考', '')):
            # 年次版更新/表示名変更のみの「変更」→ ノイズ。
            over_class = 'ノイズ'; over_noise += 1
        elif _is_renumber_noise(row):
            # 番号ずれの波及(名前が新旧両方に存在) → ノイズ。
            over_class = 'ノイズ'; over_noise += 1
        elif _is_covered(row):
            # 修正方針が対象に言及済 → 意図カバー済。
            over_class = 'カバー済'; over_covered += 1
        else:
            # 構造変化の残差 = 要確認候補。機械では詰めきれないので Claude 解釈を当てる。
            #   (修正方針は曖昧な人間記述なので、最終評価は解釈で補完する設計)
            review_needed += 1
            sig = f"{cat}/{row.get('変更種別','')}/{row.get('名称','')}"
            j = _find_over_judgement(sig)
            over_risk = j.get('risk', '') if j else ''
            over_comment = j.get('comment', '') if j else ''
            if over_risk == '妥当':
                over_class = '過剰:妥当'; over_appropriate += 1
            elif over_risk == '軽微':
                over_class = '過剰:軽微'; over_minor += 1
            elif over_risk == '重大':
                over_class = '過剰:重大'; over_critical += 1
            else:
                # 未解釈 = JIT ではこの場で Claude が解釈すべきトリガー。機械が勝手にOKにしない。
                over_class = '要確認(未解釈)'; over_unjudged += 1
        result_rows.append([
            cat,
            f'(step1側のみ/{over_class}) {row.get("名称","")}'[:120],
            f'diff_only_{over_class}',
            f"{cat}/{row.get('変更種別','')}/{row.get('名称','')}",
            row.get('ID', ''),
            over_risk, (over_comment[:80] if over_comment else ''), '-',
        ])

    # 集計
    total_intent = len(items)
    rate_mech_loose = (matched_count + partial_count) / total_intent if total_intent else 0
    final_ok = matched_count + partial_count + risk_appropriate + risk_minor
    final_rate = final_ok / total_intent if total_intent else 0

    # 最終結論 (cp932 で出力するため絵文字不可)
    #   不足側(修正方針→実装)と過剰側(実装→修正方針)の双方向で結論を出す。
    #   過剰の最終評価は Claude 解釈(overdetection_judgements)を反映。
    if risk_critical > 0 or over_critical > 0:
        parts = []
        if risk_critical > 0:
            parts.append(f'不足の重大{risk_critical}件')
        if over_critical > 0:
            parts.append(f'過剰の重大{over_critical}件')
        conclusion = '[要対応] ' + ' / '.join(parts)
    elif risk_unjudged > 0 or over_unjudged > 0:
        parts = []
        if risk_unjudged > 0:
            parts.append(f'不足側Claude未評価{risk_unjudged}件')
        if over_unjudged > 0:
            parts.append(f'過剰側Claude未解釈{over_unjudged}件')
        conclusion = '[要確認] ' + ' / '.join(parts)
    else:
        conclusion = '[OK] 実質的に問題なし'

    summary_rows = [
        ['---', '---', '---', '---', '---', '---', '---', '---'],
        ['【機械評価】', f'修正方針項目: {total_intent}件',
         f'完全マッチ: {matched_count}', f'部分マッチ: {partial_count}',
         f'未マッチ: {total_intent - matched_count - partial_count}',
         '', '', f'緩和一致率: {rate_mech_loose:.0%}'],
        ['【Claudeリスク評価】', '', '',
         f'妥当: {risk_appropriate}', f'軽微: {risk_minor}', f'重大: {risk_critical}',
         f'未評価: {risk_unjudged}', ''],
        ['【最終判定】', '', '', '', '', '', '',
         f'最終一致率: {final_rate:.0%}'],
        ['【最終結論】', conclusion, '', '', '', '', '', ''],
        ['【step1側のみ(過剰)】', f'合計: {diff_only_count}', f'要確認候補: {review_needed}', f'ノイズ: {over_noise}', f'カバー済: {over_covered}', '', '', ''],
        ['【過剰のClaude解釈】', '', '', f'妥当: {over_appropriate}', f'軽微: {over_minor}', f'重大: {over_critical}', f'未解釈: {over_unjudged}', ''],
    ]

    BugakariJSON.write_csv([HEADER] + result_rows + summary_rows, output_path)

    print(f'乖離チェック完了: {output_path}')
    print(f'  修正方針項目数: {total_intent}')
    print(f'  機械評価: 完全{matched_count} / 部分{partial_count} / 未マッチ{total_intent - matched_count - partial_count}')
    print(f'  機械評価緩和一致率: {rate_mech_loose:.0%}')
    if judgements:
        print(f'  Claudeリスク評価: 妥当{risk_appropriate} / 軽微{risk_minor} / 重大{risk_critical} / 未評価{risk_unjudged}')
        print(f'  最終一致率: {final_rate:.0%}')
    else:
        print(f'  (claude_risk_judgement.yaml なし → Claudeリスク評価未適用)')
    print(f'  最終結論: {conclusion}')
    print(f'  step1側のみ(過剰): 合計{diff_only_count} (要確認候補{review_needed} / ノイズ{over_noise} / カバー済{over_covered})')
    if over_judgements:
        print(f'  過剰のClaude解釈: 妥当{over_appropriate} / 軽微{over_minor} / 重大{over_critical} / 未解釈{over_unjudged}')
    elif review_needed > 0:
        print(f'  過剰の要確認候補(Claude解釈待ち): {over_unjudged}件')
    return final_rate, final_ok, total_intent


if __name__ == '__main__':
    if len(sys.argv) < 4:
        print('Usage: python check_alignment.py <intent_txt> <step1_diff_csv> <output_csv>')
        sys.exit(1)
    run(sys.argv[1], sys.argv[2], sys.argv[3])
