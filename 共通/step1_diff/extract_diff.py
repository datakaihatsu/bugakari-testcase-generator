"""
① 差分抽出処理
旧JSON + 新JSON → 差分レポートCSV

出力カテゴリ順: 代価表 → 計算表 → 質問 → フロー
変更種別順: 変更 → 追加 → 削除
出力列: カテゴリ, 変更種別, ID, 名称, 旧値, 新値, 備考

使い方:
  python extract_diff.py <old_json> <new_json> <output_csv>
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'engine'))
from bugakari_json import BugakariJSON

HEADER = ['カテゴリ', '変更種別', 'ID', '名称', '旧値', '新値', '備考']

CATEGORY_ORDER = {'代価表': 0, '計算表': 1, '質問': 2, '質問設定': 3, '選択肢': 4, 'フロー': 5}
CHANGE_ORDER = {'変更': 0, '追加': 1, '削除': 2}

SITSUMON_KIND_LABEL = {
    17: '数値入力',
    19: '選択式',
    205: '終点',
}


def _fmt_id(category, raw_id):
    """ID列をカテゴリに応じた日本語表記に変換"""
    if category == '質問':
        # SitsumonNo:X → 質問No:X
        return raw_id.replace('SitsumonNo:', '質問No:')
    if category == '計算表':
        # KeisanItemCD:X → 計算表変数:X
        return raw_id.replace('KeisanItemCD:', '計算表変数:')
    if category == '代価表':
        # DaikaItemCD:X → 代価表:X
        return raw_id.replace('DaikaItemCD:', '代価表:')
    # フロー: BoxNo:X のまま
    return raw_id


class DiffExtractor:

    def __init__(self, old_json: BugakariJSON, new_json: BugakariJSON):
        self.old = old_json
        self.new = new_json

    def extract_all(self):
        rows = []
        rows.extend(self._diff_daika())
        rows.extend(self._diff_keisan())
        rows.extend(self._diff_sitsumon())
        rows.extend(self._diff_sitsumon014())
        rows.extend(self._diff_sitsumon017())
        rows.extend(self._diff_sitsumon019())
        rows.extend(self._diff_flow())
        rows.sort(key=lambda r: (CATEGORY_ORDER.get(r[0], 99), CHANGE_ORDER.get(r[1], 99)))
        return rows

    # ------------------------------------------------------------------
    # 代価表（DaikaItem / DaikaItemLine）
    # ------------------------------------------------------------------

    def _diff_daika(self):
        rows = []
        old_items = {d['DaikaItemCD']: d for d in self.old.data.get('DaikaItem', [])}
        new_items = {d['DaikaItemCD']: d for d in self.new.data.get('DaikaItem', [])}

        def collect_lines(bugakari):
            counts = {}
            bikos = {}
            for line in bugakari.data.get('DaikaItemLine', []):
                cd = line.get('DaikaItemCD')
                if cd is not None:
                    counts[cd] = counts.get(cd, 0) + 1
                    biko = line.get('Biko') or ''
                    if biko:
                        bikos[cd] = (bikos.get(cd, '') + (' / ' if bikos.get(cd) else '') + biko)
            return counts, bikos

        old_lines, old_bikos = collect_lines(self.old)
        new_lines, new_bikos = collect_lines(self.new)

        def item_name(d):
            for key in ('Mesho', 'Meisho', 'Name'):
                if key in d and d[key]:
                    return d[key]
            if d.get('TankaCD'):
                return f'TankaCD:{d["TankaCD"]}'
            return f'DaikaItemCD:{d["DaikaItemCD"]}'

        for cd, item in sorted(new_items.items()):
            name = item_name(item)
            n_lines = new_lines.get(cd, 0)
            raw_id = f'DaikaItemCD:{cd}'

            if cd not in old_items:
                rows.append([
                    '代価表', '追加', _fmt_id('代価表', raw_id),
                    name, '-', f'{n_lines}行', '',
                ])
            else:
                o_lines = old_lines.get(cd, 0)
                if o_lines != n_lines:
                    rows.append([
                        '代価表', '変更', _fmt_id('代価表', raw_id),
                        name, f'{o_lines}行', f'{n_lines}行', '行数変更',
                    ])
                old_biko = old_bikos.get(cd, '')
                new_biko = new_bikos.get(cd, '')
                if old_biko != new_biko:
                    rows.append([
                        '代価表', '変更', _fmt_id('代価表', raw_id),
                        name, old_biko, new_biko, '備考変更',
                    ])

        for cd, item in sorted(old_items.items()):
            if cd not in new_items:
                name = item_name(item)
                o_lines = old_lines.get(cd, 0)
                rows.append([
                    '代価表', '削除', _fmt_id('代価表', f'DaikaItemCD:{cd}'),
                    name, f'{o_lines}行', '-', '',
                ])

        return rows

    # ------------------------------------------------------------------
    # 計算表（KeisanItem）
    # ------------------------------------------------------------------

    def _diff_keisan(self):
        rows = []
        old_map = {k['KeisanItemCD']: k for k in self.old.data.get('KeisanItem', [])}
        new_map = {k['KeisanItemCD']: k for k in self.new.data.get('KeisanItem', [])}

        for cd, item in sorted(new_map.items()):
            name = item.get('VarName') or item.get('Mesho', f'CD:{cd}')
            new_val = item.get('Value', item.get('Expression', ''))
            val_type = '固定値' if 'Value' in item else '計算式'
            raw_id = f'KeisanItemCD:{cd}'

            if cd not in old_map:
                rows.append([
                    '計算表', '追加', _fmt_id('計算表', raw_id),
                    name, '-', str(new_val), val_type,
                ])
            else:
                old_item = old_map[cd]
                old_val = old_item.get('Value', old_item.get('Expression', ''))
                if str(old_val) != str(new_val):
                    old_type = '固定値' if 'Value' in old_item else '計算式'
                    note = val_type if old_type == val_type else f'{old_type}→{val_type}'
                    rows.append([
                        '計算表', '変更', _fmt_id('計算表', raw_id),
                        name, str(old_val), str(new_val), note,
                    ])

        for cd, item in sorted(old_map.items()):
            if cd not in new_map:
                name = item.get('VarName') or item.get('Mesho', f'CD:{cd}')
                old_val = item.get('Value', item.get('Expression', ''))
                rows.append([
                    '計算表', '削除', _fmt_id('計算表', f'KeisanItemCD:{cd}'),
                    name, str(old_val), '-', '',
                ])

        return rows

    # ------------------------------------------------------------------
    # 質問（SitsumonItem）
    # ------------------------------------------------------------------

    def _diff_sitsumon(self):
        rows = []
        old_map = {s['SitsumonNo']: s for s in self.old.data.get('SitsumonItem', [])}
        new_map = {s['SitsumonNo']: s for s in self.new.data.get('SitsumonItem', [])}

        for no, item in sorted(new_map.items()):
            raw_id = f'SitsumonNo:{no}'
            if no not in old_map:
                kind = item.get('SitsumonKind', '')
                kind_label = SITSUMON_KIND_LABEL.get(kind, f'SitsumonKind:{kind}')
                rows.append([
                    '質問', '追加', _fmt_id('質問', raw_id),
                    item.get('Mesho', ''),
                    '-',
                    kind_label,
                    item.get('SitsumonVersion', ''),
                ])
            else:
                old_item = old_map[no]
                changes = []
                if old_item.get('Mesho') != item.get('Mesho'):
                    changes.append('表示名変更')
                if old_item.get('SitsumonVersion') != item.get('SitsumonVersion'):
                    changes.append('バージョン更新')
                if changes:
                    rows.append([
                        '質問', '変更', _fmt_id('質問', raw_id),
                        item.get('Mesho', ''),
                        old_item.get('Mesho', ''),
                        item.get('Mesho', ''),
                        ' / '.join(changes),
                    ])

        for no, item in sorted(old_map.items()):
            if no not in new_map:
                rows.append([
                    '質問', '削除', _fmt_id('質問', f'SitsumonNo:{no}'),
                    item.get('Mesho', ''),
                    '-',
                    '-',
                    item.get('SitsumonVersion', ''),
                ])

        return rows

    # ------------------------------------------------------------------
    # サブルーチン呼び出し設定（Sitsumon014）― 送り変数(SendItemList)の変更
    # ------------------------------------------------------------------

    def _diff_sitsumon014(self):
        rows = []
        old_map = {e['SitsumonNo']: e for e in self.old.data.get('Sitsumon014', [])}
        new_map = {e['SitsumonNo']: e for e in self.new.data.get('Sitsumon014', [])}

        all_nos = sorted(set(list(old_map.keys()) + list(new_map.keys())))
        for no in all_nos:
            sit_name = (
                self.new.get_sitsumon_name(no) if no in new_map
                else self.old.get_sitsumon_name(no)
            )

            if no not in old_map or no not in new_map:
                continue  # 質問自体の追加・削除は 質問 カテゴリで処理済み

            old_sends = {
                item['SendVarName']: item['CurVarName']
                for item in old_map[no].get('SendItemList', [])
            }
            new_sends = {
                item['SendVarName']: item['CurVarName']
                for item in new_map[no].get('SendItemList', [])
            }

            all_send_vars = sorted(set(list(old_sends.keys()) + list(new_sends.keys())))
            for send_var in all_send_vars:
                old_cur = old_sends.get(send_var)
                new_cur = new_sends.get(send_var)
                if old_cur == new_cur:
                    continue
                name = f'{sit_name} 送り変数:{send_var}'
                if old_cur is None:
                    rows.append([
                        '質問設定', '追加', f'質問No:{no}',
                        name, '-',
                        f'親:{new_cur}→子:{send_var}', '送り変数追加',
                    ])
                elif new_cur is None:
                    rows.append([
                        '質問設定', '削除', f'質問No:{no}',
                        name, f'親:{old_cur}→子:{send_var}',
                        '-', '送り変数削除',
                    ])
                else:
                    rows.append([
                        '質問設定', '変更', f'質問No:{no}',
                        name,
                        f'親:{old_cur}→子:{send_var}',
                        f'親:{new_cur}→子:{send_var}',
                        '送り変数変更',
                    ])
        return rows

    # ------------------------------------------------------------------
    # 質問設定（Sitsumon017）― 規格名計上(IsKikakuKeijo)・接続変数など
    # ------------------------------------------------------------------

    def _diff_sitsumon017(self):
        rows = []

        def entry_key(item):
            return (item['SitsumonNo'], item.get('SerialNo', 1))

        old_map = {entry_key(s): s for s in self.old.data.get('Sitsumon017', [])}
        new_map = {entry_key(s): s for s in self.new.data.get('Sitsumon017', [])}

        def summarize(item):
            parts = []
            if 'VarName' in item:
                parts.append(f'変数:{item["VarName"]}')
            if 'KeisanItemCD' in item:
                parts.append(f'計算表変数:{item["KeisanItemCD"]}')
            parts.append(f'規格名計上:{"あり" if item.get("IsKikakuKeijo") else "なし"}')
            if item.get('TaniMesho'):
                parts.append(f'単位:{item["TaniMesho"]}')
            return ' / '.join(parts)

        for k, item in sorted(new_map.items()):
            no, serial = k
            name = item.get('VarName', f'SerialNo:{serial}')

            if k not in old_map:
                rows.append([
                    '質問設定', '追加', f'質問No:{no}',
                    name, '-', summarize(item),
                    f'規格名計上:{"あり" if item.get("IsKikakuKeijo") else "なし"}',
                ])
            else:
                old_item = old_map[k]
                changed = [
                    f for f in ('VarName', 'KeisanItemCD', 'IsKikakuKeijo', 'TaniMesho', 'DefaultValue')
                    if old_item.get(f) != item.get(f)
                ]
                if changed:
                    rows.append([
                        '質問設定', '変更', f'質問No:{no}',
                        name,
                        summarize(old_item),
                        summarize(item),
                        '変更項目: ' + ', '.join(changed),
                    ])

        for k, item in sorted(old_map.items()):
            if k not in new_map:
                no, serial = k
                name = item.get('VarName', f'SerialNo:{serial}')
                rows.append([
                    '質問設定', '削除', f'質問No:{no}',
                    name, summarize(item), '-', '',
                ])

        return rows

    # ------------------------------------------------------------------
    # 選択肢（Sitsumon019.SitTabRows）― 既存質問への行追加・削除のみ
    # セル値（計設定値等）はUIに表示されないため対象外
    # ------------------------------------------------------------------

    def _diff_sitsumon019(self):
        rows = []

        # 新規追加・削除された質問はスキップ（質問カテゴリで既に表示済み）
        old_nos = {s['SitsumonNo'] for s in self.old.data.get('SitsumonItem', [])}
        new_nos = {s['SitsumonNo'] for s in self.new.data.get('SitsumonItem', [])}
        existing_nos = old_nos & new_nos

        old_map = {
            s['SitsumonNo']: s for s in self.old.data.get('Sitsumon019', [])
            if s['SitsumonNo'] in existing_nos
        }
        new_map = {
            s['SitsumonNo']: s for s in self.new.data.get('Sitsumon019', [])
            if s['SitsumonNo'] in existing_nos
        }

        def row_text(sit, row_id):
            """SitTabCells の最終列から選択肢の表示テキストを取得"""
            if sit is None:
                return f'RowID:{row_id}'
            cells = {
                (c['RowID'], c['ColID']): c.get('Value', '')
                for c in sit.get('SitTabCells', [])
            }
            max_col = max((c['ColID'] for c in sit.get('SitTabCols', [])), default=1)
            text = cells.get((row_id, max_col), f'RowID:{row_id}')
            return str(text).replace('\r\n', ' ').strip()

        for no in sorted(set(old_map) | set(new_map)):
            sit_name = self.new.get_sitsumon_name(no)
            if '不明' in sit_name:
                sit_name = self.old.get_sitsumon_name(no)

            old_sit = old_map.get(no)
            new_sit = new_map.get(no)

            old_row_ids = {r['RowID'] for r in (old_sit.get('SitTabRows', []) if old_sit else [])}
            new_row_ids = {r['RowID'] for r in (new_sit.get('SitTabRows', []) if new_sit else [])}

            for row_id in sorted(new_row_ids - old_row_ids):
                rows.append([
                    '選択肢', '追加', f'質問No:{no}',
                    sit_name, '-', row_text(new_sit, row_id), '',
                ])

            for row_id in sorted(old_row_ids - new_row_ids):
                rows.append([
                    '選択肢', '削除', f'質問No:{no}',
                    sit_name, row_text(old_sit, row_id), '-', '',
                ])

        return rows

    # ------------------------------------------------------------------
    # フロー（FlowItems）
    # ------------------------------------------------------------------

    def _diff_flow(self):
        rows = []
        old_map = {f['BoxNo']: f for f in self.old.data.get('FlowItems', [])}
        new_map = {f['BoxNo']: f for f in self.new.data.get('FlowItems', [])}

        for box_no, item in sorted(new_map.items()):
            sit_no = item.get('SitsumonNo')
            name = self.new.get_sitsumon_name(sit_no) if sit_no else f'BoxNo:{box_no}'
            new_callbox = [c for c in item.get('CallBox', []) if c > 0]

            if box_no not in old_map:
                callbox_display = ', '.join(
                    self.new.resolve_boxno_name(c) for c in new_callbox
                ) if new_callbox else '-'
                rows.append([
                    'フロー', '追加', f'BoxNo:{box_no}',
                    name, '-', f'質問No:{sit_no}',
                    f'CallBox→[{callbox_display}]' if new_callbox else '',
                ])
            else:
                old_item = old_map[box_no]
                old_sit_no = old_item.get('SitsumonNo')
                old_callbox = [c for c in old_item.get('CallBox', []) if c > 0]

                if old_sit_no != sit_no or old_callbox != new_callbox:
                    rows.append([
                        'フロー', '変更', f'BoxNo:{box_no}',
                        name,
                        f'質問No:{old_sit_no} CallBox:{old_callbox}',
                        f'質問No:{sit_no} CallBox:{new_callbox}',
                        '',
                    ])

        for box_no, item in sorted(old_map.items()):
            if box_no not in new_map:
                sit_no = item.get('SitsumonNo')
                name = self.old.get_sitsumon_name(sit_no) if sit_no else f'BoxNo:{box_no}'
                rows.append([
                    'フロー', '削除', f'BoxNo:{box_no}',
                    name, f'質問No:{sit_no}', '-', '',
                ])

        return rows


# ------------------------------------------------------------------

def run(old_json_path, new_json_path, output_path):
    old_json = BugakariJSON(old_json_path)
    new_json = BugakariJSON(new_json_path)

    extractor = DiffExtractor(old_json, new_json)
    rows = extractor.extract_all()

    BugakariJSON.write_csv([HEADER] + rows, output_path)

    print(f'差分レポート生成完了: {output_path}')
    print(f'  合計: {len(rows)}件')
    for cat in ['代価表', '計算表', '質問', '質問設定', '選択肢', 'フロー']:
        adds = sum(1 for r in rows if r[0] == cat and r[1] == '追加')
        changes = sum(1 for r in rows if r[0] == cat and r[1] == '変更')
        deletes = sum(1 for r in rows if r[0] == cat and r[1] == '削除')
        print(f'  {cat}: 変更{changes} 追加{adds} 削除{deletes}')


if __name__ == '__main__':
    if len(sys.argv) < 4:
        print('Usage: python extract_diff.py <old_json> <new_json> <output_csv>')
        sys.exit(1)
    run(sys.argv[1], sys.argv[2], sys.argv[3])
