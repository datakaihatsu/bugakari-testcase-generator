"""
外部/計設定変数シナリオの追加生成 (任意・フラグ制御)

差分(新質問・選択肢追加・計算式の分岐追加)が、外部/計設定変数の非既定分岐の奥に
あるために既定フローでは到達/検証できないケース(例: #35 大阪市 msk=4 /
#34 O~atk==4 / #36 O~SAM==1 / #24 被災地=O~pre==17)で、その変数を差分値に
シードした別シナリオのテストケースを追加生成する。

【設計 — 既存工種への影響ゼロ】
- 本体パイプライン(step1〜3)は不変。既定の step3.0_テストケース.csv は不変。
- 後段の追加パスとして外部変数シードの一時 new_json で step2/step3 を再実行し
  step3.0_テストケース_<変数=値[_選択肢]>.csv を追加出力する。
- pipeline フラグ(既定OFF、run_koshu は自動ON)で制御。

【2トリガー】
  T1(新質問): 差分の新質問が auto確定(外部変数駆動)ゲートの奥にあり、
              ユーザー選択では到達できない場合(例 #35 msk)。
  T2(計算分岐): 計算変数の式に「外部変数==値」の新分岐が差分で追加(例 #34 O~atk==4)。
  ※ シード値が変数の既定値と同じ場合は除外。ファイル名に「変数=値[_選択肢テキスト]」。

【シナリオTCの区分・観点】
  追加生成したシナリオは「今回追加/変更された外部分岐」の検証なので、
  テスト区分を『差分』に補正し、「選択肢の適切さ確認」に分岐検証の観点を付す。
"""

import os
import re
import csv
import sys
import json
import tempfile

BASE = os.path.dirname(os.path.dirname(__file__))  # 02_第2弾_G条件/
sys.path.insert(0, os.path.join(BASE, 'engine'))
sys.path.insert(0, os.path.join(BASE, 'step2_proposals'))
sys.path.insert(0, os.path.join(BASE, 'step3_csv'))

from bugakari_json import BugakariJSON
from flow_walker import FlowWalker


def _load_json(path):
    with open(path, encoding='utf-8-sig') as f:
        return json.load(f)


def _owned_vars(data):
    owned = set()
    for s in data.get('Sitsumon019', []):
        for c in s.get('SitCols', []):
            if c.get('VarName'):
                owned.add(c['VarName'])
    for s in data.get('SitsumonItem', []):
        if s.get('VarName'):
            owned.add(s['VarName'])
    for s in data.get('Sitsumon017', []):
        if s.get('VarName'):
            owned.add(s['VarName'])
    return owned


def _is_external(v, defined_expr, owned):
    if not v:
        return False
    if v.startswith('O~') or v.startswith('O‾'):
        return True
    return (v not in owned) and (v not in defined_expr)


def _root_external(v, by_expr, owned, _seen=None):
    """v が外部変数(またはその単一エイリアス)なら、根の外部変数名を返す。でなければ None。
    例 (#99): SE~P は KeisanItem 定義 Expression='O~Sep' の単一エイリアス → 根 O~Sep(O~接頭辞=外部)。
    エイリアス連鎖を辿り、O~接頭辞 or 未定義(=外部) に行き着けばそれを返す。"""
    if not v:
        return None
    _seen = _seen or set()
    if v in _seen:
        return None
    _seen.add(v)
    if v.startswith('O~') or v.startswith('O‾'):
        return v
    e = (by_expr.get(v) or '').strip()
    if e and re.fullmatch(r"[A-Za-z~][A-Za-z0-9~_\']*", e):
        r = _root_external(e, by_expr, owned, _seen)
        if r:
            return r
    # 定義(式/値)も所有(質問変数)も無い = 外部入力変数
    if (v not in by_expr) and (v not in owned):
        return v
    return None


def _joken_value(joken):
    mk, mv = joken.get('MaxKigou'), joken.get('MaxValue')
    nk, nv = joken.get('MinKigou'), joken.get('MinValue')
    if mk in (1, 3) and mv is not None:
        return float(mv)
    if nk == 1 and nv is not None:
        return float(nv)
    if mk == 2 and mv is not None:
        return float(mv) - 1
    if nk == 2 and nv is not None:
        return float(nv) + 1
    return None


def _sanitize(label):
    label = re.sub(r'[\\/:*?"<>|\s]+', '_', (label or '').strip())
    return label[:40] or 'scenario'


def _row_display(s019, row_id):
    """ゲート行の選択肢表示テキスト(SitTabCells の非数値文字列セル)を返す。
    例: #35 モルタル選択区分 Row7 = '配合比選択区分(大阪市)'。最長の文字列を採用。"""
    best = ''
    for c in (s019 or {}).get('SitTabCells', []):
        if c.get('RowID') != row_id:
            continue
        v = c.get('Value')
        if isinstance(v, str):
            t = v.strip()
            if t and not re.fullmatch(r'-?\d+(?:\.\d+)?', t) and len(t) > len(best):
                best = t
    return best


_EQ_COND = re.compile(r'([A-Za-z~‾][A-Za-z0-9~‾_]*)\s*==\s*(-?\d+(?:\.\d+)?)')


def _eq_conditions(expr):
    if not expr:
        return set()
    return {(m.group(1), float(m.group(2))) for m in _EQ_COND.finditer(expr)}


def _default_value(v, new):
    if v in ('O~Sys', 'O‾Sys'):
        return 1.0
    for k in new.get('KeisanItem', []):
        if k.get('VarName') == v and k.get('Value') is not None:
            try:
                return float(k['Value'])
            except Exception:
                return None
    return None


def _calc_branch_candidates(old, new, defined_expr, owned):
    old_expr = {k.get('VarName'): k.get('Expression') for k in old.get('KeisanItem', [])}
    out = {}
    for k in new.get('KeisanItem', []):
        v = k.get('VarName')
        ne = k.get('Expression')
        if not v or not ne:
            continue
        new_conds = _eq_conditions(ne) - _eq_conditions(old_expr.get(v))
        for (cv, cval) in new_conds:
            if not _is_external(cv, defined_expr, owned):
                continue
            dv = _default_value(cv, new)
            if dv is not None and dv == cval:
                continue
            out.setdefault((cv, cval), f'{cv}={cval:g}')
    return [(cv, cval, lab) for (cv, cval), lab in out.items()]


def detect_scenarios(old_json_path, new_json_path):
    old = _load_json(old_json_path)
    new = _load_json(new_json_path)
    bj = BugakariJSON(new_json_path)

    old_nos = {s['SitsumonNo'] for s in old.get('SitsumonItem', [])}
    new_qs = {s['SitsumonNo'] for s in new.get('SitsumonItem', [])
              if s['SitsumonNo'] not in old_nos}

    defined_expr = {k.get('VarName') for k in new.get('KeisanItem', [])
                    if k.get('VarName') and k.get('Expression')}
    owned = _owned_vars(new)
    # T1過剰抑制(#99): AutoSelectJoken を多数(>=ENUM_TH)駆動する変数は「選択肢列挙ドライバ」
    #   (例 H30j1=鉄筋規格コード・101行駆動)であり、外部分岐ではない。各値でシナリオ生成すると
    #   規格選択が爆発する(27件)。通常の vary 軸で網羅されるため T1 では除外する。
    from collections import Counter as _Counter
    _autojoken_drive = _Counter(
        (r.get('AutoSelectJoken') or {}).get('VarName')
        for s in new.get('Sitsumon019', []) for r in s.get('SitTabRows', [])
        if (r.get('AutoSelectJoken') or {}).get('VarName'))
    _ENUM_TH = 10

    scenarios = []
    seen = set()

    # T2: 計算分岐
    for (cv, cval, lab) in _calc_branch_candidates(old, new, defined_expr, owned):
        if (cv, cval) in seen:
            continue
        seen.add((cv, cval))
        scenarios.append({'var': cv, 'value': cval, 'label': lab,
                          'newly_reached': [], 'kind': 'calc'})

    # T1: 新質問
    if not new_qs:
        return scenarios

    base_res = FlowWalker(bj).walk()
    base_reached = set(base_res['visited_sitsumons'])
    base_sources = base_res.get('row_sources', {})
    base_unreached_new = new_qs - base_reached
    if not base_unreached_new:
        return scenarios

    user_reachable = set(base_reached)
    rows_budget = 80
    spent = 0
    for s in new.get('Sitsumon019', []):
        sn = s['SitsumonNo']
        if base_sources.get(sn) == 'auto':
            continue
        sel = [r['RowID'] for r in s.get('SitRows', [])
               if r.get('Visible', True) and not r.get('IsFixed', False)]
        if len(sel) < 2:
            continue
        for rid in sel:
            if spent >= rows_budget:
                break
            spent += 1
            user_reachable |= set(
                FlowWalker(bj, vary_selections={sn: rid}).walk()['visited_sitsumons'])
        if spent >= rows_budget:
            break
    over_budget = spent >= rows_budget

    cand = {}
    for s in new.get('Sitsumon019', []):
        for r in s.get('SitTabRows', []):
            j = r.get('AutoSelectJoken') or {}
            v = j.get('VarName')
            if not _is_external(v, defined_expr, owned):
                continue
            if _autojoken_drive.get(v, 0) >= _ENUM_TH:
                continue  # 選択肢列挙ドライバ(規格コード等)はvary軸で網羅・外部分岐でない
            val = _joken_value(j)
            if val is None:
                continue
            dv = _default_value(v, new)
            if dv is not None and dv == val:
                continue
            disp = _row_display(s, r.get('RowID'))
            label = f'{v}={val:g}' + (f'_{disp}' if disp else '')
            cand.setdefault((v, val), label)

    for (v, val), label in cand.items():
        if (v, val) in seen:
            continue
        seeded = FlowWalker(bj, vary_selections=None)
        try:
            seeded.hyo.set_input(v, val)
        except Exception:
            continue
        reached = set(seeded.walk()['visited_sitsumons'])
        newly = (base_unreached_new & reached) - user_reachable
        if newly:
            seen.add((v, val))
            scenarios.append({'var': v, 'value': val, 'label': label,
                              'newly_reached': sorted(newly), 'kind': 'question',
                              'user_reach_budget_exceeded': over_budget})

    # T3: 新規タブ(SitTab)の TabJoken が外部変数==値で開く (#99 東京都建設局タブ=SE~P==13)。
    #   タブはフロー到達でなく TabJoken 条件で表示される表示バリアントなので、
    #   AutoSelectJoken と同じ要領で条件を読み、外部(エイリアス解決含む)なら別シナリオ生成。
    by_expr = {k.get('VarName'): k.get('Expression')
               for k in new.get('KeisanItem', []) if k.get('VarName')}

    def _tab_key(t):
        return (t.get('SitsumonNo'), t.get('TabNo', 0), t.get('TabMesho'))
    old_tab_keys = {_tab_key(t) for t in old.get('SitTab', [])}
    for t in new.get('SitTab', []):
        if _tab_key(t) in old_tab_keys:
            continue  # 新規追加タブのみ
        j = t.get('TabJoken') or {}
        rv = _root_external(j.get('VarName'), by_expr, owned)
        if not rv:
            continue
        val = _joken_value(j)
        if val is None:
            continue
        dv = _default_value(rv, new)
        if dv is not None and dv == val:
            continue
        if (rv, val) in seen:
            continue
        seen.add((rv, val))
        tmesho = (t.get('TabMesho') or '').strip()
        scenarios.append({'var': rv, 'value': val,
                          'label': f'{rv}={val:g}' + (f'_{tmesho}' if tmesho else ''),
                          'newly_reached': [], 'kind': 'tab'})

    return scenarios


def _annotate_scenario_csv(tc_path, label):
    """シナリオTCを補正: テスト区分を『差分』に、「選択肢の適切さ確認」に分岐検証観点を付与。
    (このファイルは今回追加/変更された外部条件の分岐＝回帰ではない)"""
    try:
        with open(tc_path, encoding='cp932') as f:
            rows = list(csv.reader(f))
    except Exception:
        return
    if len(rows) < 2:
        return
    hdr = rows[0]
    i_kbn = hdr.index('テスト区分') if 'テスト区分' in hdr else None
    i_chk = next((i for i, c in enumerate(hdr) if c.startswith('選択肢の適切さ確認')), None)
    note = f'・【外部条件 {label}】の分岐挙動が外部設計どおりか（今回追加/変更された経路）'
    default_chk = '・選択肢が商品(現行版)と変わっていないこと'
    for r in rows[1:]:
        if i_kbn is not None and i_kbn < len(r):
            r[i_kbn] = '差分'
        if i_chk is not None and i_chk < len(r):
            cur = (r[i_chk] or '').strip()
            if not cur or cur in (default_chk, '-'):
                r[i_chk] = note
            else:
                r[i_chk] = note + '\n' + cur
    try:
        with open(tc_path, 'w', encoding='cp932', newline='') as f:
            csv.writer(f).writerows(rows)
    except Exception:
        pass


def generate(old_json_path, new_json_path, output_dir, run_step2, run_step3):
    scenarios = detect_scenarios(old_json_path, new_json_path)
    if not scenarios:
        return []
    new = _load_json(new_json_path)
    made = []
    for sc in scenarios:
        v, val, label = sc['var'], sc['value'], sc['label']
        seeded = json.loads(json.dumps(new))
        found = False
        for k in seeded.get('KeisanItem', []):
            if k.get('VarName') == v:
                k['Value'] = val
                k['Expression'] = None
                found = True
        if not found:
            seeded.setdefault('KeisanItem', []).append(
                {'VarName': v, 'Value': val, 'Expression': None})
        tmp = tempfile.NamedTemporaryFile(
            mode='w', suffix='.json', delete=False, encoding='utf-8')
        json.dump(seeded, tmp, ensure_ascii=False)
        tmp.close()
        try:
            safe = _sanitize(label)
            plan_path = os.path.join(output_dir, f'step2.0_テスト計画_{safe}.csv')
            tc_path = os.path.join(output_dir, f'step3.0_テストケース_{safe}.csv')
            run_step2(os.path.join(output_dir, 'step1.0_差分レポート.csv'),
                      tmp.name, plan_path, old_json_path)
            run_step3(plan_path, tmp.name, tc_path, old_json_path)
            _annotate_scenario_csv(tc_path, label)
            sc['file'] = tc_path
            made.append(sc)
            print(f'  [外部シナリオ追加] {label} -> {os.path.basename(tc_path)} (新規到達: {sc["newly_reached"]})')
        finally:
            os.unlink(tmp.name)
    return made
