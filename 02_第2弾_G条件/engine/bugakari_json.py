"""
歩掛JSONパーサー・解析ユーティリティ（共通エンジン）
"""

import json
import csv
import io

# Expression評価器 (Being.Core.Expressions.Parser.cs 準拠)
from expression import (
    Evaluator,
    KeisanHyo,
    ExpressionError,
    ExternalReferenceError,
)

__all__ = [
    'BugakariJSON', 'fmt', 'build_joken_shiki',
    'Evaluator', 'KeisanHyo', 'ExpressionError', 'ExternalReferenceError',
]


class _ShortCutResolvingDict(dict):
    """Sitsumon019 索引。自分の SitsumonNo に定義が無ければ ShortCut 先をたどって返す。

    ShortCut 質問(SitsumonItem.ShortCutSitsumonNo 持ち)は Sitsumon019 を自分では持たず、
    選択肢の定義は canonical 側にある(内部仕様書 追補1)。計画段(step2)がこれを解決せず
    「選択肢0件」と数えると、本来 vary の質問が fix(選択肢1件)に落ち、強制行IDも空になり
    「表示は①・計算は②」の食い違ったテストケースが出る(確定設計B / 2件目 原因1・2)。
    索引の参照一点で解決することで、計画段・表示段・差分モードの全箇所に同時に効く。
    """

    def __init__(self, mapping, owner):
        super().__init__(mapping)
        self._owner = owner

    def get(self, key, default=None):
        v = dict.get(self, key)
        if v is not None:
            return v
        seen = set()
        cur = key
        while cur is not None and cur not in seen:
            seen.add(cur)
            sc = (self._owner.sitsumon_by_no.get(cur) or {}).get('ShortCutSitsumonNo')
            if not sc or sc in seen:
                break
            v = dict.get(self, sc)
            if v is not None:
                return v
            cur = sc
        return default


class BugakariJSON:

    def __init__(self, path):
        with open(path, encoding='utf-8-sig') as f:
            self.data = json.load(f)
        self._build_index()

    def _build_index(self):
        self.sitsumon_by_no = {
            s['SitsumonNo']: s for s in self.data.get('SitsumonItem', [])
        }
        self.sitsumon019_by_no = _ShortCutResolvingDict(
            {s['SitsumonNo']: s for s in self.data.get('Sitsumon019', [])}, self
        )
        self.keisan_by_varname = {
            k['VarName']: k
            for k in self.data.get('KeisanItem', [])
            if 'VarName' in k
        }
        self.flow_by_boxno = {
            f['BoxNo']: f for f in self.data.get('FlowItems', [])
        }

    # ------------------------------------------------------------------
    # SF・省庁区分
    # ------------------------------------------------------------------

    def get_sf_value(self):
        """SF変数の固定値を取得。Expression（計算式）の場合はNoneを返す"""
        sf = self.keisan_by_varname.get('SF')
        if sf and 'Value' in sf and 'Expression' not in sf:
            return sf['Value']
        return None

    def is_province_auto_selected(self):
        return self.get_sf_value() is not None

    def resolve_province_name(self):
        sf_val = self.get_sf_value()
        if sf_val is None:
            return None
        for s in self.data.get('SitsumonItem', []):
            if s.get('Mesho') == '省庁区分' and s.get('SitsumonExecuteKind') == 2:
                sit019 = self.sitsumon019_by_no.get(s['SitsumonNo'])
                if sit019 is None:
                    continue
                cells = {
                    (c['RowID'], c['ColID']): c.get('Value', '')
                    for c in sit019.get('SitTabCells', [])
                }
                for row in sit019.get('SitTabRows', []):
                    joken = row.get('AutoSelectJoken', {})
                    if not joken:
                        continue
                    if joken.get('MaxKigou') == 3:
                        if sf_val <= joken.get('MaxValue', float('inf')):
                            max_col = max(
                                (c['ColID'] for c in sit019.get('SitTabCols', [])),
                                default=1
                            )
                            return cells.get((row['RowID'], max_col), f'RowID:{row["RowID"]}')
        return f'SF={sf_val}（省庁名解決不可）'

    # ------------------------------------------------------------------
    # FlowItems: BoxNo → Sitsumon名解決
    # ------------------------------------------------------------------

    def get_sitsumon_name(self, sitsumon_no):
        s = self.sitsumon_by_no.get(sitsumon_no)
        if s:
            return s.get('Mesho', f'SitsumonNo:{sitsumon_no}')
        return f'SitsumonNo:{sitsumon_no}（不明）'

    def resolve_boxno_name(self, box_no):
        flow = self.flow_by_boxno.get(box_no)
        if flow and 'SitsumonNo' in flow:
            return self.get_sitsumon_name(flow['SitsumonNo'])
        return f'BoxNo:{box_no}（Sitsumon未定義）'

    def resolve_callbox_names(self, box_no):
        flow = self.flow_by_boxno.get(box_no)
        if flow is None:
            return []
        return [self.resolve_boxno_name(cb) for cb in flow.get('CallBox', []) if cb > 0]

    # ------------------------------------------------------------------
    # Sitsumon019: 選択肢テキスト
    # ------------------------------------------------------------------

    def get_sitsumon_choices(self, sitsumon_no):
        sit = self.sitsumon019_by_no.get(sitsumon_no)
        if sit is None:
            return []
        cells = {
            (c['RowID'], c['ColID']): c.get('Value', '')
            for c in sit.get('SitTabCells', [])
        }
        max_col = max(
            (c['ColID'] for c in sit.get('SitTabCols', [])), default=1
        )
        choices = []
        for row in sit.get('SitTabRows', []):
            if row.get('AutoSelectJoken'):
                text = cells.get((row['RowID'], max_col), '').replace('\r\n', ' ')
                if text:
                    choices.append(text)
        return choices

    # ------------------------------------------------------------------
    # タブ解決 (SitTab / TabJoken)
    #   1質問が複数タブを持つ場合 (例: 「被災地補正なし/あり」)、TabJoken を
    #   現在の変数スコープで評価して有効タブを決定する。SitTabCells は TabNo
    #   ごとに別値を持ちうる (TabNo 省略=基本タブ)。
    # ------------------------------------------------------------------

    def tabs_for(self, sitsumon_no):
        return [t for t in self.data.get('SitTab', []) if t.get('SitsumonNo') == sitsumon_no]

    def active_tab_no(self, sitsumon_no, hyo):
        """現在の変数スコープ(hyo)で有効な TabNo を返す。
        - タブが0/1個 → そのタブの TabNo (なければ None=基本)
        - 複数タブ → 登場順に TabJoken を評価し、最初に成立したタブ。
          TabJoken が空(基本タブ)なら常に成立扱い。
        - どれも評価不能/不成立 → 先頭タブ。
        """
        tabs = self.tabs_for(sitsumon_no)
        if len(tabs) <= 1:
            return tabs[0].get('TabNo') if tabs else None
        for tab in tabs:
            joken = tab.get('TabJoken') or {}
            shiki = build_joken_shiki(joken)
            if shiki is None:
                return tab.get('TabNo')  # 基本タブ (条件なし) は常に有効
            try:
                if hyo.evaluate(shiki) != 0:
                    return tab.get('TabNo')
            except (ExpressionError, ExternalReferenceError):
                continue
        return tabs[0].get('TabNo')

    @staticmethod
    def cell_value_for_tab(sit019, row_id, col_id, active_tab):
        """指定タブを優先してセル値を取得。
        active_tab に一致するセルがあればそれを、なければ基本タブ(TabNo省略)を使う。
        """
        match = None
        base = None
        for c in sit019.get('SitTabCells', []):
            if c.get('RowID') == row_id and c.get('ColID') == col_id:
                tn = c.get('TabNo')
                if tn == active_tab:
                    match = c
                if tn is None:
                    base = c
        chosen = match if match is not None else base
        return chosen.get('Value') if chosen is not None else None

    # ------------------------------------------------------------------
    # KeisanItem: 変数値取得
    # ------------------------------------------------------------------

    def get_keisan_value(self, var_name):
        k = self.keisan_by_varname.get(var_name)
        if k and 'Value' in k:
            return k['Value']
        return None

    # ------------------------------------------------------------------
    # 差分検出
    # ------------------------------------------------------------------

    def detect_new_sitsumons(self, old_json):
        old_nos = {s['SitsumonNo'] for s in old_json.data.get('SitsumonItem', [])}
        return [
            s for s in self.data.get('SitsumonItem', [])
            if s['SitsumonNo'] not in old_nos
        ]

    def detect_changed_keisan(self, old_json):
        old_map = {k['KeisanItemCD']: k for k in old_json.data.get('KeisanItem', [])}
        changes = []
        for k in self.data.get('KeisanItem', []):
            old_k = old_map.get(k['KeisanItemCD'])
            if old_k is None:
                changes.append({'type': 'new', 'item': k})
            elif (k.get('Value') != old_k.get('Value') or
                  k.get('Expression') != old_k.get('Expression')):
                changes.append({'type': 'changed', 'old': old_k, 'new': k})
        return changes

    def detect_new_daika_items(self, old_json):
        old_ids = {d['DaikaItemCD'] for d in old_json.data.get('DaikaItem', [])}
        return [
            d for d in self.data.get('DaikaItem', [])
            if d['DaikaItemCD'] not in old_ids
        ]

    # ------------------------------------------------------------------
    # CSV出力
    # ------------------------------------------------------------------

    @staticmethod
    def write_csv(rows, out_path):
        """Shift-JIS(cp932) CSV出力。Excelで開くために必須"""
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator='\r\n')
        for row in rows:
            writer.writerow(row)
        with open(out_path, 'w', encoding='cp932', newline='') as f:
            f.write(buf.getvalue())


# ------------------------------------------------------------------
# Joken → 評価式 (TabJoken / AutoSelectJoken 共通)
#   Kigou: 1=Equal(==), 2=Greater(<), 3=GreatThanEqual(<=)
#   flow_walker._build_shiki と同一セマンティクス。
# ------------------------------------------------------------------

def build_joken_shiki(joken):
    var = joken.get('VarName')
    if not var:
        return None
    op_map = {1: '==', 2: '<', 3: '<='}
    min_kigou = joken.get('MinKigou', 0)
    max_kigou = joken.get('MaxKigou', 0)
    min_val = joken.get('MinValue')
    max_val = joken.get('MaxValue')
    parts = []
    if min_kigou and min_val is not None:
        parts.append(f'{min_val}{op_map.get(min_kigou, "==")}{var}')
    if max_kigou and max_val is not None:
        parts.append(f'{var}{op_map.get(max_kigou, "==")}{max_val}')
    return ' && '.join(parts) if parts else None


# ------------------------------------------------------------------
# 数値フォーマットユーティリティ
# ------------------------------------------------------------------

def fmt(v, digits=3):
    """数値を小数digits桁で整形（末尾ゼロは1桁残す）"""
    if v is None:
        return ''
    r = round(v, digits)
    s = f'{r:.{digits}f}'.rstrip('0')
    if s.endswith('.'):
        s += '0'
    return s


# ======================================================================
# 確定設計C: 選択肢の表示文字列と数量入力の仕様（①③で共用）
# ======================================================================

def _is_numeric_text(v):
    try:
        float(str(v))
        return True
    except (TypeError, ValueError):
        return False


def choice_display_map(sit019):
    """Sitsumon019 の選択可能行 → 表示文字列 の対応を返す（確定設計C C-R1/R2/R2'）。

    ①(gen_gjoken._g_options) と ③(generate_csv._get_axis_rows) が同じ列を選ぶための
    唯一の実装。従来は①「VarNameなし優先」/③「VarName持ち優先」と逆で、同じ質問の
    選択肢が「30km以上 60km未満」と「0.1」に食い違っていた（4件目①）。

    列の選び方（辞書順で最大）:
        1. 選択可能行すべてに値がある列か   … 一部だけ値がある列(Row4 表示の原因)を避ける
        2. VarName を持たない列か           … 変数列(計算値)より名称列
        3. 非数値セルの数                   … 数値コードより文字
        4. 値の種類数                       … 行を区別できる列
    それでも行の値が重複する場合は、他の文字列列を**表の列順(ColID昇順)**に ' / ' で
    連結して一意化する（空欄の列は飛ばす）。40 不整地運搬車規格選択のように単一列では
    5行が3択に畳まれる質問への対応。

    戻り値: (disp_map {row_id: 表示文字列}, disp_col, extra_cols)
    """
    cells = {}
    for c in sit019.get('SitTabCells', []):
        cells[(c.get('RowID'), c.get('ColID'))] = c.get('Value', '')
    sit_rows = sit019.get('SitRows', [])
    sel = [r['RowID'] for r in sit_rows
           if r.get('Visible', True) and not r.get('IsFixed', False)]
    cols = [c for c in sit019.get('SitCols', []) if c.get('Visible', True)]

    def _vals(col_id):
        return [str(cells.get((r, col_id), '') or '').replace('\r\n', ' ').strip()
                for r in sel]

    disp_col, best = None, None
    for c in cols:
        cid = c.get('ColID')
        vals = _vals(cid)
        nz = [v for v in vals if v]
        if not nz:
            continue
        score = (1 if len(nz) == len(sel) else 0,
                 0 if c.get('VarName') else 1,
                 sum(1 for v in nz if not _is_numeric_text(v)),
                 len(set(nz)))
        if best is None or score > best:
            best, disp_col = score, cid
    if disp_col is None:
        return {r: f'Row{r}' for r in sel}, None, []

    extra_cols = []
    cur = _vals(disp_col)
    for c in cols:
        if len(set(cur)) == len(cur):
            break
        cid = c.get('ColID')
        if cid == disp_col or c.get('VarName'):
            continue
        vals = _vals(cid)
        if all((not v) or _is_numeric_text(v) for v in vals):
            continue
        joined = [(a + ' / ' + b) if b else a for a, b in zip(cur, vals)]
        if len(set(joined)) > len(set(cur)):
            extra_cols.append(cid)
            cur = joined

    use_cols = sorted([disp_col] + extra_cols)
    disp_map = {}
    for r in sel:
        parts = [str(cells.get((r, cid), '') or '').replace('\r\n', ' ').strip()
                 for cid in use_cols]
        parts = [p for p in parts if p]
        disp_map[r] = ' / '.join(parts) if parts else f'Row{r}'
    return disp_map, disp_col, extra_cols


# BugakariKigouEnum [確定/ソース BugakariDefine.cs]: 0=None(制限なし) 1=「＜」 2=「≦」 3=「＝」
#   ※ Sitsumon017.IsInRange は Equal(3) を default(制限なし)として扱う
_KIGOU_OP = {1: '<', 2: '<='}


def numeric_input_spec(bj, sitsumon_no):
    """数量入力(Kind17)の単位・範囲・既定値を返す（確定設計C C-R5/R7）。

    Sitsumon017 の定義は canonical 側にのみ存在し ShortCut 子は持たない（内部仕様書 追補1）。
    自分の番号でしか探さないと定義が見つからず `(値なし)` になる（4件目②-a）。
    Min/Max は非null decimal で、JSON にキーが無ければ 0（内部仕様書 追補3・Sirius確定）。

    戻り値: None（数量入力でない） / {'var','unit','default','format','range'}
            range は '0 < 値' / '0 < 値 <= 100' 形式。制限なしなら ''
    """
    cands, seen, cur = [], set(), sitsumon_no
    while cur is not None and cur not in seen:
        seen.add(cur)
        cands.append(cur)
        sc = (bj.sitsumon_by_no.get(cur) or {}).get('ShortCutSitsumonNo')
        if not sc or sc in seen:
            break
        cur = sc
    for sn in cands:
        for e in bj.data.get('Sitsumon017', []):
            if e.get('SitsumonNo') != sn or not e.get('VarName'):
                continue
            lo = _KIGOU_OP.get(e.get('MinKigou', 0) or 0, '')
            hi = _KIGOU_OP.get(e.get('MaxKigou', 0) or 0, '')
            rng = ''
            if lo and hi:
                rng = f"{fmt_num(e.get('Min', 0))} {lo} 値 {hi} {fmt_num(e.get('Max', 0))}"
            elif lo:
                rng = f"{fmt_num(e.get('Min', 0))} {lo} 値"
            elif hi:
                rng = f"値 {hi} {fmt_num(e.get('Max', 0))}"
            return {'var': e.get('VarName'), 'unit': (e.get('TaniMesho') or '').strip(),
                    'default': e.get('DefaultValue'), 'format': e.get('Format'),
                    'range': rng}
    return None


def fmt_num(v):
    """範囲表示用の数値整形（1.0 → 1、0.50 → 0.5）。"""
    try:
        d = float(v)
    except (TypeError, ValueError):
        return str(v)
    s = f'{d:.6f}'.rstrip('0').rstrip('.')
    return s or '0'
