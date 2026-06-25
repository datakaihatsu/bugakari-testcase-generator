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


class BugakariJSON:

    def __init__(self, path):
        with open(path, encoding='utf-8-sig') as f:
            self.data = json.load(f)
        self._build_index()

    def _build_index(self):
        self.sitsumon_by_no = {
            s['SitsumonNo']: s for s in self.data.get('SitsumonItem', [])
        }
        self.sitsumon019_by_no = {
            s['SitsumonNo']: s for s in self.data.get('Sitsumon019', [])
        }
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
