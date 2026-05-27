"""
歩掛JSONパーサー・解析ユーティリティ（共通エンジン）
"""

import json
import csv
import io


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
