"""
フロー走査エンジン

代価表行フラグ (Mesho "N=代価表X枚目Y行目" パターン) も追跡:
  訪問した Sit の Mesho を解析し、各代価表行が active(1) / inactive(0) かを記録。
  TC ごとに、inactive な行に対応する S* 出力を空欄にできる。

新JSON の FlowItems を traverse して、特定のシナリオ(vary軸の値の組み合わせ)で
実際に訪問される Sitsumon を抽出する。

【ロジック】
- Start ボックス (FlowItemFlags=[0]) から開始
- 各ボックスを訪問:
  - FlowKind=1 (Sit): SitsumonNo を訪問記録、その Sitsumon の選択行で変数を更新、CallBox[0] へ進む
  - FlowKind=2 (Bunki): SitsumonNo を訪問記録、選択行を判定し対応する CallBox[i] へ進む
  - FlowKind=3 (Command): CallBox[0] へ進む
  - FlowKind=4 (End): 終了
  - FlowKind=5 (Through): CallBox[0] へ進む

【選択行の判定】
- AutoSelectJoken が真の行がある → その行 (auto)
- そうでなければ、vary軸として指定された行 (vary_selections)
- そうでなければ、SitTab.DefaultRowID の行 (デフォルト)
- それでもなければ、最初の選択可能行

【分岐(Bunki)時の CallBox インデックス対応】
CallBox[i] が i+1 番目の「選択可能行(Visible & not IsFixed)」に対応すると仮定
"""

import sys
import os
import re
from decimal import Decimal

sys.path.insert(0, os.path.dirname(__file__))
from expression import KeisanHyo, ExpressionError, ExternalReferenceError

# Mesho "N=代価表X枚目Y行目" のパターン (代価表行 active/inactive フラグ)
_DAIKA_ROW_FLAG_PATTERN = re.compile(r'^(\d+(?:\.\d+)?)=代価表(\d+)枚目(\d+)行目$')


class FlowWalker:

    # ボックス訪問の最大数(無限ループ防止)
    MAX_VISITS = 10000

    def __init__(self, bugakari_json, vary_selections=None):
        """
        bugakari_json: BugakariJSON インスタンス
        vary_selections: dict[SitsumonNo → row_id] vary 軸の選択値
        """
        self.bj = bugakari_json
        self.data = bugakari_json.data
        # レベル変数==1 で「閉じた」質問 (UI 非表示で自動確定) の記録
        self.closed_sitsumons = set()
        # パネル対応: ボックスを (PanelNo, BoxNo) で管理 (複数パネルで BoxNo が衝突するため)
        self.boxes = {}
        for f in self.data.get('FlowItems', []):
            self.boxes[(f.get('PanelNo', 1), f['BoxNo'])] = f
        # 各パネルの開始ボックス (Flags=[0] があればそれ、無ければ最小BoxNo)
        self.panel_start = {}
        for f in self.data.get('FlowItems', []):
            pn = f.get('PanelNo', 1)
            flags = f.get('FlowItemFlags', [])
            if isinstance(flags, list) and 0 in flags and pn not in self.panel_start:
                self.panel_start[pn] = f['BoxNo']
        for (pn, bn) in self.boxes:
            if pn not in self.panel_start:
                self.panel_start[pn] = min(b for (q, b) in self.boxes if q == pn)
        # PanelMesho → PanelNo (フロー切替の解決用)
        self.panel_by_mesho = {}
        for t in self.data.get('FlowTitles', []):
            m = t.get('PanelMesho')
            if m:
                self.panel_by_mesho[m] = t.get('PanelNo')
        self.vary_selections = vary_selections or {}
        self.s019 = bugakari_json.sitsumon019_by_no

        # KeisanHyo (var設定の伝搬を扱うために mutate していく)
        # strict_undefined=True: 親から渡される変数等が未確定なら AutoSelect 評価でエラー→skip
        self.hyo = KeisanHyo(self.data.get('KeisanItem', []), strict_undefined=True)
        # 環境前提値: O~Sys(積算システム) は SekisanEnv 連動変数で、実行環境では 1。
        #   JSON 内に Value/Expression が無い場合のみシードする (06 購入土 ZK=if(O~Sys==1,1,..)
        #   が実機で 1 → 「計上する」自動確定、の再現に必要)。
        _osys_defined = any(
            k.get('VarName') == 'O~Sys' and (k.get('Value') is not None or k.get('Expression'))
            for k in self.data.get('KeisanItem', [])
        )
        if not _osys_defined:
            try:
                self.hyo.set_input('O~Sys', 1)
            except Exception:
                pass
        # 各 Sitsumon でどう行を選んだか ('auto', 'vary', 'default', 'first')
        self.row_sources = {}

    # ------------------------------------------------------------------
    # Start box 検索
    # ------------------------------------------------------------------

    def find_start(self):
        """主流フロー(デフォルト)の開始位置 (PanelNo, BoxNo) を返す。
        Flags=[0] のうち最小 PanelNo (=主流パネル) を採用。"""
        cands = []
        for f in self.data.get('FlowItems', []):
            flags = f.get('FlowItemFlags', [])
            if isinstance(flags, list) and 0 in flags:
                cands.append((f.get('PanelNo', 1), f['BoxNo']))
        if cands:
            cands.sort()
            return cands[0]
        return None

    def _resolve_switch_panel(self, mesho):
        """「フロー切替:X」 の X を PanelMesho と照合し対象 PanelNo を返す。"""
        m = mesho or ''
        x = m.split(':', 1)[1].strip() if ':' in m else m.strip()
        return self.panel_by_mesho.get(x)

    # ------------------------------------------------------------------
    # 走査 (単一パス)
    # ------------------------------------------------------------------

    def walk(self):
        """
        単一パスで走査(vary軸は self.vary_selections で固定)。
        戻り値: {
            'visited_sitsumons': [SitsumonNo,...] (順序通り、重複あり),
            'sit_selections':     {SitsumonNo: row_id} (各Sitsumonでどの行を選んだか),
            'scope':              {VarName: Decimal} (最終的なスコープ)
        }
        """
        start = self.find_start()
        if start is None:
            return {'visited_sitsumons': [], 'sit_selections': {}, 'scope': {}}

        visited_sits = []
        sit_selections = {}
        visit_count = 0

        # パネル対応走査:
        #   主流パネル(Panel1)から開始。「フロー切替」ボックスで対象パネルを
        #   サブルーチン呼び出しし、サブパネルの終点で呼び出し元へ復帰する。
        cur_panel, current_box_no = start
        call_stack = []  # [(復帰先パネル, 復帰先BoxNo)]
        while current_box_no is not None:
            visit_count += 1
            if visit_count > self.MAX_VISITS:
                break

            box = self.boxes.get((cur_panel, current_box_no))
            if box is None:
                if call_stack:
                    cur_panel, current_box_no = call_stack.pop()
                    continue
                break

            kind = box.get('FlowKind', 0)
            cb_all = box.get('CallBox', [])
            sit_no = box.get('SitsumonNo')
            sit_item = self.bj.sitsumon_by_no.get(sit_no, {}) if sit_no else {}

            # フロー切替 (SitsumonKind=119): 対象パネルをサブルーチン呼び出し
            if sit_item.get('SitsumonKind') == 119:
                target = self._resolve_switch_panel(sit_item.get('Mesho', ''))
                ret_box = cb_all[0] if (cb_all and cb_all[0] >= 0) else None
                if target is not None and target in self.panel_start and target != cur_panel:
                    if ret_box is not None:
                        call_stack.append((cur_panel, ret_box))
                    cur_panel = target
                    current_box_no = self.panel_start[target]
                    continue
                current_box_no = ret_box
                if current_box_no is None and call_stack:
                    cur_panel, current_box_no = call_stack.pop()
                continue

            # 終端: CallBox が空 or 全て負 → サブパネルなら復帰、主流なら終了
            if not cb_all or all(c < 0 for c in cb_all):
                if call_stack:
                    cur_panel, current_box_no = call_stack.pop()
                    continue
                break

            if kind == 1 or kind == 2:  # Sit or Bunki
                if sit_no:
                    visited_sits.append(sit_no)
                    chosen_row = self._choose_row(sit_no)
                    if chosen_row is not None:
                        sit_selections[sit_no] = chosen_row
                        self._apply_row_vars(sit_no, chosen_row)
                    if kind == 2:
                        next_box_no = self._bunki_next_box(box, sit_no, chosen_row)
                    else:
                        next_box_no = cb_all[0] if cb_all else None
                else:
                    next_box_no = cb_all[0] if cb_all else None
            else:  # Command, Through, None
                next_box_no = cb_all[0] if cb_all else None

            current_box_no = next_box_no

        scope = dict(self.hyo._user_inputs)
        daika_row_flags = self._extract_daika_row_flags(visited_sits)
        return {
            'visited_sitsumons': visited_sits,
            'sit_selections': sit_selections,
            'row_sources': dict(self.row_sources),
            'scope': scope,
            'daika_row_flags': daika_row_flags,
        }

    def _extract_daika_row_flags(self, visited_sits):
        """訪問順に Mesho "N=代価表X枚目Y行目" をデコードし、(sheet, row) → value を返す。
        同じ (sheet, row) が複数回現れたら最後の値が有効。
        """
        sit_by_no = {s['SitsumonNo']: s for s in self.data.get('SitsumonItem', [])}
        flags = {}
        for sn in visited_sits:
            s = sit_by_no.get(sn)
            if not s:
                continue
            mesho = s.get('Mesho', '')
            m = _DAIKA_ROW_FLAG_PATTERN.match(mesho)
            if m:
                val = float(m.group(1))
                sheet = int(m.group(2))
                row = int(m.group(3))
                flags[(sheet, row)] = val
        return flags

    # ------------------------------------------------------------------
    # Row 選択ロジック
    # ------------------------------------------------------------------

    def _choose_row(self, sitsumon_no):
        """選択行を決定。優先: vary > auto > default > 先頭
        副作用: self.row_sources[sitsumon_no] に選択経緯を記録 ('vary'/'auto'/'default'/'first')
        """
        # 0. レベル変数: 現在スコープで level==1 なら質問は「閉じる」
        #    → ユーザー選択(vary)は無効で、AutoSelectJoken/デフォルトが決める。
        #    例: 06 撤去(C=3)で LEVsk=1 → 資材計上区分が閉じ rsskH=2 で「施工費のみ」に自動確定。
        #    開いている(0,2等)場合は従来どおり vary 優先。評価不能時は開扱い(安全側)。
        _closed = False
        _sit = self.bj.sitsumon_by_no.get(sitsumon_no, {})
        _lv = _sit.get('LevelVarName')
        if _lv:
            try:
                _closed = float(self.hyo.value(_lv)) == 1.0
            except Exception:
                _closed = False
        if _closed:
            self.closed_sitsumons.add(sitsumon_no)

        # 1. vary 軸として指定されていればそれ (閉じている場合を除く)
        if sitsumon_no in self.vary_selections and not _closed:
            self.row_sources[sitsumon_no] = 'vary'
            return self.vary_selections[sitsumon_no]

        sit019 = self.s019.get(sitsumon_no)
        if sit019 is None:
            # Sitsumon017 等は Sitsumon019 を持たない
            return None

        # 2. AutoSelectJoken で auto-selectable な行
        # strict モードのため、未確定変数を含む joken は ExpressionError で skip される
        for row in sit019.get('SitTabRows', []):
            joken = row.get('AutoSelectJoken')
            if not joken or not (joken.get('VarName') or joken.get('Shiki')):
                continue
            shiki = joken.get('Shiki') or self._build_shiki(joken)
            if not shiki:
                continue
            try:
                result = self.hyo.evaluate(shiki)
                if result != 0:
                    self.row_sources[sitsumon_no] = 'auto'
                    return row['RowID']
            except (ExternalReferenceError, ExpressionError):
                continue

        # 3. デフォルト行 (SitTab.DefaultRowID)
        for tab in self.data.get('SitTab', []):
            if tab.get('SitsumonNo') == sitsumon_no:
                d = tab.get('DefaultRowID')
                if d:
                    self.row_sources[sitsumon_no] = 'default'
                    return d
                break

        # 4. 最初の選択可能行
        for sr in sit019.get('SitRows', []):
            if sr.get('Visible', True) and not sr.get('IsFixed', False):
                self.row_sources[sitsumon_no] = 'first'
                return sr['RowID']
        return None

    def _build_shiki(self, joken):
        var = joken.get('VarName')
        if not var:
            return None
        # Kigou: 1=Equal, 2=Greater(<), 3=GreatThanEqual(<=)
        op_map = {1: '==', 2: '<', 3: '<='}
        min_kigou = joken.get('MinKigou', 0)
        max_kigou = joken.get('MaxKigou', 0)
        min_val = joken.get('MinValue')
        max_val = joken.get('MaxValue')
        parts = []
        if min_kigou and min_val is not None:
            op = op_map.get(min_kigou, '==')
            parts.append(f'{min_val}{op}{var}')
        if max_kigou and max_val is not None:
            # Min側の指定が無い単独の MaxKigou=3 は「==」(一致) 判定 (_joken_selects と同解釈)
            if max_kigou == 3 and not (min_kigou and min_val is not None):
                op = '=='
            else:
                op = op_map.get(max_kigou, '==')
            parts.append(f'{var}{op}{max_val}')
        return ' && '.join(parts) if parts else None

    # ------------------------------------------------------------------
    # 変数伝搬: 選択行の Cell から VarName 列の値を scope に設定
    # ------------------------------------------------------------------

    def _apply_row_vars(self, sitsumon_no, row_id):
        sit019 = self.s019.get(sitsumon_no)
        if sit019 is None:
            return
        var_cols = [c for c in sit019.get('SitCols', []) if c.get('VarName')]
        if not var_cols:
            return
        # Fix B: タブ対応。複数タブ質問は現在の変数スコープ(self.hyo)で有効タブを
        #   決め、そのタブのセル値を伝搬する (例: 04 No:3 日当り施工量 →
        #   被災地補正なし=630 / あり=567)。J30 等のタブ判定変数は、フロー上で
        #   先行する質問(被災地 No:1)が既に設定済み。
        active_tab = self.bj.active_tab_no(sitsumon_no, self.hyo)
        for vc in var_cols:
            val = self.bj.cell_value_for_tab(sit019, row_id, vc['ColID'], active_tab)
            if val is None or val == '':
                continue
            try:
                self.hyo.set_input(vc['VarName'], val)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Bunki の遷移先決定
    # ------------------------------------------------------------------

    def _bunki_next_box(self, box, sitsumon_no, chosen_row):
        """選択行に対応する CallBox を返す"""
        cb = box.get('CallBox', [])
        if not cb:
            return None
        if chosen_row is None:
            return cb[0]

        sit019 = self.s019.get(sitsumon_no)
        if sit019 is None:
            return cb[0]

        selectable_rows = [
            r['RowID']
            for r in sit019.get('SitRows', [])
            if r.get('Visible', True) and not r.get('IsFixed', False)
        ]
        try:
            idx = selectable_rows.index(chosen_row)
        except ValueError:
            return cb[0]
        if idx < len(cb):
            return cb[idx]
        return cb[-1]
