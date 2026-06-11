"""
② テスト計画生成 (絞り込みアプローチ)

【設計思想】
v1 (旧案) の問題: 差分の名称マッチで vary 軸を探すため、同名の別 Sit を誤採用していた。
v2 (新案) の解: 差分の SitsumonNo を直接活用し、baseline で訪問されない vary 候補は
前段 Sit の選択肢を forward 試行して「到達経路」を自動探索する。

【入力】
- step1.0_差分レポート.csv (差分情報)
- 新JSON

【出力】
- step2.0_テスト計画.csv
  HEADER: 軸ID, 軸名, 種別, SitsumonNo, 列ラベル, 変更理由, 備考, 強制行ID
  ※ 「強制行ID」 列は新案で新規追加。絞り込みで決まった probe Sit の固定行を指定する。
"""

import sys
import os
import csv

try:
    import yaml
except ImportError:
    yaml = None

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'engine'))
from bugakari_json import BugakariJSON, KeisanHyo, ExternalReferenceError, ExpressionError
from flow_walker import FlowWalker

_YAML_PATH = os.path.join(os.path.dirname(__file__), '..', 'knowledge', 'global_rules.yaml')

HEADER = [
    '軸ID', '軸名', '種別', 'SitsumonNo', '列ラベル', '変更理由', '備考', '強制行ID',
]


def load_global_rules(path=_YAML_PATH):
    if yaml is None or not os.path.exists(path):
        return {}
    with open(path, encoding='utf-8') as f:
        return yaml.safe_load(f) or {}


class TestPlanGenerator:

    def __init__(self, diff_csv_path, new_json_path, old_json_path=None):
        self.new_json = BugakariJSON(new_json_path)
        self.old_json = BugakariJSON(old_json_path) if old_json_path else None
        self.diff_rows = self._load_diff(diff_csv_path) if diff_csv_path else []
        self._counter = 0
        self.global_rules = load_global_rules()
        self._baseline_visited = None
        self._baseline_visit_seq = None
        self._baseline_scope = None
        self._baseline_scope_dict = None

    def _load_diff(self, path):
        if not path:
            return []
        with open(path, encoding='cp932', newline='') as f:
            return list(csv.DictReader(f))

    def _next_id(self):
        self._counter += 1
        return f'AX-{self._counter:03d}'

    # ------------------------------------------------------------------
    # 差分から vary 候補の SitsumonNo を抽出
    # ------------------------------------------------------------------

    def _vary_targets_from_diff(self):
        targets = {}
        for row in self.diff_rows:
            cat = row.get('カテゴリ')
            kind = row.get('変更種別')
            rid = row.get('ID', '')
            no = self._parse_sitsumon_no(rid)
            if no is None:
                continue
            if cat == '質問' and kind == '追加':
                targets.setdefault(no, []).append('新規追加質問')
            elif cat == '選択肢' and kind == '追加':
                targets.setdefault(no, []).append('選択肢追加')
            elif cat == '選択肢' and kind == '削除':
                targets.setdefault(no, []).append('選択肢削除')
            elif cat == '選択肢' and kind == '変更':
                # 選択肢テキスト変更 (文字修正)。規格名計上の検証等のため軸に上げる
                #   (step3 で最長テキストの選択肢1件に絞る = 新提案A)
                targets.setdefault(no, []).append('選択肢テキスト変更')
        return targets

    @staticmethod
    def _parse_sitsumon_no(rid):
        if '質問No:' in rid:
            try:
                return int(rid.split('質問No:')[1].strip())
            except ValueError:
                pass
        return None

    # ------------------------------------------------------------------
    # 過去リリース由来の「質問追加」判定
    # ------------------------------------------------------------------

    def _is_stale_addition(self, sit):
        """旧JSONが古い(複数年前)場合、過去年度に追加済みの質問が step1 で
        「質問追加」として検出される。SitsumonVersion が新JSONの LastUpdateDate より
        1年以上古い質問は、今回の変更ではなく過去リリース由来 → 既存質問(fix)扱い。
        例: 05 吹付プラント設備(2021/04追加, 旧JSON=2020年版) は 2026年修正方針の差分ではない。
        """
        from datetime import datetime, timedelta
        lud = self.new_json.data.get('LastUpdateDate')
        sv = sit.get('SitsumonVersion')
        if not lud or not sv:
            return False
        try:
            d_l = datetime.strptime(str(lud).split()[0], '%Y/%m/%d')
            d_s = datetime.strptime(str(sv).split()[0], '%Y/%m/%d')
        except Exception:
            return False
        return d_s < d_l - timedelta(days=365)

    # ------------------------------------------------------------------
    # J3: デフォルト実行 判定 (ユーザに問われずデフォルト選択で固定実行)
    # ------------------------------------------------------------------

    @staticmethod
    def _is_default_exec(sit):
        """J3 条件: SitsumonExecuteKind=1 かつ SitsumonFlags が {105,108} のみ
        (105=IsGaia9SitsumonItem, 108=Gaia9関連) かつ SitsumonKind≠17 (数値入力でない)。
        1,2,3,100 等の SekisanEnv 連動フラグを持つものは未確定 → 対象外 (安全側で fix/vary 維持)。
        """
        if sit.get('SitsumonExecuteKind') != 1:
            return False
        if sit.get('SitsumonKind') == 17:
            return False
        extra_flags = [f for f in (sit.get('SitsumonFlags') or []) if f not in (105, 108)]
        return not extra_flags

    def _all_rows_autoselect(self, sit):
        """B-15: 選択可能行が全て AutoSelectJoken 条件付きの質問は、駆動変数が
        ユーザ入力(JSON内未解決)でも実機では自動確定され、ユーザは直接選ばない。
        例: 15 Sit11 日当り架設質量(トラッククレーン架設) = W の範囲表
        (0<W<=20 / 20<W<=35 / 35<W<=60)。列に出すと誤解を招くため auto 扱い。"""
        no = sit.get('SitsumonNo')
        s019 = self.new_json.sitsumon019_by_no.get(no)
        if not s019:
            return False
        sel = [r['RowID'] for r in s019.get('SitRows', [])
               if r.get('Visible', True) and not r.get('IsFixed', False)]
        if not sel:
            return False
        aj = {r['RowID']: (r.get('AutoSelectJoken') or {})
              for r in s019.get('SitTabRows', [])}
        if not all(aj.get(rid) for rid in sel):
            return False
        # 駆動変数が「数値入力質問(Kind17)が書く変数」の場合のみ auto。
        #   実行時は上流のユーザ入力で必ず確定する範囲表 (例: 15 W/L2)。
        #   駆動変数が空の計算表変数 (00 cs / 01 A3 / 06 FG / 02 FG2 等) は
        #   質問自身の選択で値が決まる = ユーザが選ぶ質問 → 列に残す。
        input_vars = {(e.get('VarName') or '').strip()
                      for e in self.new_json.data.get('Sitsumon017', []) or []}
        input_vars.discard('')
        drv = {(aj[rid].get('VarName') or '').strip() for rid in sel}
        return bool(drv) and all(v and v in input_vars for v in drv)

    def _is_kind8_echo(self, sit):
        """Kind=8 が「分岐の選択肢ごとのマスタ展開(エコー)」かどうか。
        親の分岐(FlowKind=2)の遷移先に Kind=8 が2つ以上ある場合、選択は分岐質問側で
        既に表されており、Kind=8 はその帰結 → 列にしない。
        単独配置の Kind=8 (06 材料の選択など) はユーザーの選択箇所 → 列にする。
        """
        no = sit.get('SitsumonNo')
        fis = self.new_json.data.get('FlowItems', [])
        sit_by_no = self.new_json.sitsumon_by_no
        my_boxes = {(f.get('PanelNo', 1), f.get('BoxNo')) for f in fis if f.get('SitsumonNo') == no}
        if not my_boxes:
            return False
        box_by_key = {(f.get('PanelNo', 1), f.get('BoxNo')): f for f in fis}
        for parent in fis:
            if parent.get('FlowKind') != 2:
                continue
            pn = parent.get('PanelNo', 1)
            targets = [(pn, b) for b in (parent.get('CallBox') or []) if isinstance(b, int) and b > 0]
            if not (my_boxes & set(targets)):
                continue
            k8 = 0
            for key in targets:
                tb = box_by_key.get(key)
                if tb is not None:
                    ts = sit_by_no.get(tb.get('SitsumonNo'), {})
                    if ts.get('SitsumonKind') == 8:
                        k8 += 1
            if k8 >= 2:
                return True
        return False

    # ------------------------------------------------------------------
    # UI 可視判定
    # ------------------------------------------------------------------

    def _is_ui_visible_axis(self, sit):
        kind = sit.get('SitsumonKind')
        # Kind=8 (単価マスタ選択): ユーザー実行(ExecKind=2)のものだけ列対象。
        #   例: 06 材料の選択(土のう/土砂)=表示、02 軽油(ExecKind=None,非対話)=非表示。
        #   ただし「分岐から複数のKind=8へ扇状展開」(例: 00 砕石の種類→種類別マスタ群)は
        #   既に分岐質問が列になっており Kind=8 は選択のエコー → 非表示。
        # 07-2: Kind=8 (単価マスタ選択) は「単価選択する質問」であり、歩掛の条件
        #   ではない (例: 07 再生砂は敷材料の種類に従属して単価を選ぶだけ)。
        #   07 フィードバックにより全工種共通で列から除外に統一。
        #   (旧: ExecKind=2 のものは列対象。06 材料の選択(土砂) もこの統一で除外)
        if kind == 8:
            return False
        if kind not in (17, 19):
            return False
        # 自動決定(AutoSelectJokenの駆動変数がJSON内で確定)の質問は、ユーザが選ばない
        #   帰結表示なので軸(列)から除外する。例: 02 積算区分(136/149), 03 省庁区分/資料区分。
        if self._is_autodetermined(sit):
            return False
        # J6: ShortCutSitsumonNo を持つ場合、ShortCut先 Sit が UI 可視判定をパスする
        #     なら ShortCut元 Sit も UI 表示される (画面に出る参照表示)。
        #     ShortCut先が除外対象 (MinKigou 内部分岐 等) なら ShortCut元も除外。
        sc_no = sit.get('ShortCutSitsumonNo')
        if sc_no:
            sc_sit = next((s for s in self.new_json.data.get('SitsumonItem', [])
                           if s.get('SitsumonNo') == sc_no), None)
            if sc_sit is None:
                return False
            # 二段以上のチェーンは想定外として除外
            if sc_sit.get('ShortCutSitsumonNo'):
                return False
            # ShortCut先の UI 可視判定を再帰
            return self._is_ui_visible_axis(sc_sit)
        no = sit.get('SitsumonNo')
        mesho = sit.get('Mesho', '')
        if '=' in mesho:
            left = mesho.split('=', 1)[0]
            has_japanese_in_left = any(ord(c) >= 128 for c in left)
            if not has_japanese_in_left:
                return False
        sit019 = self.new_json.sitsumon019_by_no.get(no)
        # レベル変数を持つ質問は開閉がレベル最終値で決まる (上の _is_autodetermined で判定済み)。
        #   行構造ヒューリスティックで誤って内部分岐扱いしない (例: 07 資材計上区分 Sit21)。
        if sit019 is not None and not sit.get('LevelVarName'):
            rows = sit019.get('SitTabRows', [])
            with_joken = sum(
                1 for r in rows
                if r.get('AutoSelectJoken')
                and (r['AutoSelectJoken'].get('VarName') or r['AutoSelectJoken'].get('Shiki'))
            )
            if len(rows) <= 4 and with_joken == 1:
                return False
            has_min_kigou = False
            joken_count = 0
            min_kigou_var = None
            for r in rows:
                joken = r.get('AutoSelectJoken')
                if not joken:
                    continue
                if not (joken.get('VarName') or joken.get('Shiki')):
                    continue
                joken_count += 1
                mk = joken.get('MinKigou')
                mv = joken.get('MinValue')
                if mk and mv is not None:
                    has_min_kigou = True
                    min_kigou_var = joken.get('VarName')
                    break
            # A'-2 改良: MinKigou(下限付き範囲条件)で行が決まる質問でも、
            #   駆動変数が「確定する」(計算式/固定値を持つ or 上流質問が設定する)
            #   ものだけ内部自動選択として除外する。
            #   駆動変数が「自由な外部変数」(計算式なし・固定値なし・どの質問も
            #   設定しない = 例: 01 作業船の選択 A3) の場合、その質問こそが
            #   ユーザの選択箇所なので可視に残す。
            if joken_count > 0 and has_min_kigou:
                if self._var_is_determined(min_kigou_var):
                    return False
                # 自由変数 → ユーザ選択 → 可視 (fall through)
        return True

    def _var_is_determined(self, varname):
        """駆動変数が確定するか。
        True  = 計算式(Expression) か 固定値(Value) を持つ、または上流の
                Sitsumon019列(VarName)が設定する → 到達時には値が確定 → 内部自動選択。
        False = いずれにも該当しない自由な外部変数 → ユーザが選ぶ箇所。
        """
        if not varname:
            return False
        k = self.new_json.keisan_by_varname.get(varname)
        if k and (k.get('Expression') or k.get('Value') is not None):
            return True
        for s in self.new_json.data.get('Sitsumon019', []):
            for c in s.get('SitCols', []):
                if c.get('VarName') == varname:
                    return True
        return False

    def _autoselect_vars_of(self, sit):
        """質問(またはShortCut先)の SitTabRows AutoSelectJoken 駆動変数を集める。"""
        no = sit.get('SitsumonNo')
        s019 = self.new_json.sitsumon019_by_no.get(no)
        if not s019:
            sc = sit.get('ShortCutSitsumonNo')
            if sc:
                s019 = self.new_json.sitsumon019_by_no.get(sc)
        vs = []
        if s019:
            for r in s019.get('SitTabRows', []):
                j = r.get('AutoSelectJoken') or {}
                v = j.get('VarName')
                if v:
                    vs.append(v)
        return list(dict.fromkeys(vs))

    def _is_determined_source(self, v):
        """駆動変数が「出所のある(=自由な外部変数でない)」値か。
        スコープに解決済み / KeisanItem に Value(定数) / Expression(計算式) を持つ → True。
        いずれも無い(式も値も無く、ユーザ/マスタ入力に委ねられる) → False(自由)。
        """
        if not v:
            return False
        self._baseline_walk()
        if v in (self._baseline_scope or set()):
            return True
        k = self.new_json.keisan_by_varname.get(v)
        return bool(k and (k.get('Value') is not None or k.get('Expression')))

    def _resolve_value(self, v):
        """駆動変数の最終値を非strict評価(未定義は既定値)で求める。実積算の自動選択に近い。"""
        from bugakari_json import KeisanHyo
        hyo = KeisanHyo(self.new_json.data.get('KeisanItem', []))
        for vn, val in (self._baseline_scope_dict or {}).items():
            try:
                hyo.set_input(vn, val)
            except Exception:
                pass
        try:
            return float(hyo.value(v))
        except Exception:
            return None

    @staticmethod
    def _joken_selects(joken, value):
        """AutoSelectJoken の範囲条件を value が満たすか。Kigou: 1=Equal,2=Greater(< / >),3=<=。"""
        if value is None:
            return False
        ok = True
        mk, mv = joken.get('MaxKigou'), joken.get('MaxValue')
        nk, nv = joken.get('MinKigou'), joken.get('MinValue')
        has_min = (nk in (1, 2)) and (nv is not None)
        if mk == 3 and mv is not None:
            # Min側の指定が無い単独の MaxKigou=3 は「==」(一致) 判定。
            #   根拠: 07 時間的制約 JIK=-1(番兵値)/施工規模 F=0/夜間 F2=0 は行を選ばず開く。
            #   Min側がある場合は範囲条件の上限 (<=)。
            ok = ok and (value == mv if not has_min else value <= mv)
        elif mk == 2 and mv is not None:
            ok = ok and value < mv
        elif mk == 1 and mv is not None:
            ok = ok and value == mv
        if nk == 2 and nv is not None:
            ok = ok and value > nv
        elif nk == 1 and nv is not None:
            ok = ok and value == nv
        return ok

    def _level_value_is(self, sit, value):
        """レベル変数(LevelVarName)の評価値が value か。仕様§1.5: 1=計設定/2=デフォルト選択/3=必ず実行。"""
        lv = sit.get('LevelVarName')
        if not lv:
            return False
        self._baseline_walk()
        v = self._resolve_value(lv)
        try:
            return v is not None and float(v) == float(value)
        except Exception:
            return False

    def _is_autodetermined(self, sit):
        """自動決定(=軸から除外すべき)か。
        条件: AutoSelectJoken のいずれかの行が、
          (a) その駆動変数が「出所あり」(_is_determined_source) で、
          (b) その変数の最終値が当該行の範囲条件を満たす(自動選択される)
        を両方満たす → 自動決定 → 除外。
        ※ 自由な外部変数(A3 等)で行が選ばれても、ユーザ/マスタ選択なので除外しない。
        ※ 計算式を持つ変数でも、最終値がどの行も選ばなければ(例 04 F~BIG=1.0)
          ユーザが選ぶ状態 → 除外しない。
        ※ レベル変数(LevelVarName)/被災地補正等もこの「最終値→行選択」判定で正しく分かれる。
        """
        # レベル変数ルール: LevelVarName を持つ質問は、レベル変数の最終値で開閉が決まる。
        #   最終値 1 = 閉じる(自動確定) → 除外 / それ以外(0,2,…) = 開く → 表示。
        #   例: 04 道路維持 LEV_A=2→表示, 04 被災地 Q~hk2=1→非表示,
        #       06 資材計上区分 LEVsk=0→表示, 06 購入土計上区分 LEVzk=0→表示。
        lv = sit.get('LevelVarName')
        if lv:
            self._baseline_walk()
            v = self._resolve_value(lv)
            try:
                return v is not None and float(v) == 1.0
            except Exception:
                return False
        no = sit.get('SitsumonNo')
        s019 = self.new_json.sitsumon019_by_no.get(no)
        if not s019:
            sc = sit.get('ShortCutSitsumonNo')
            if sc:
                s019 = self.new_json.sitsumon019_by_no.get(sc)
        if not s019:
            return False
        self._baseline_walk()
        for r in s019.get('SitTabRows', []):
            # 選択不可行 (RowFlags=1=IsCannotSelectRow) は自動選択されない
            #   → この行が条件成立しても製品は自動確定しないため、自動決定判定から除外
            #   根拠: Sitsumon019Test.cs:211-233 / 仕様書§1.3・ギャップ#4 (2026-06-05)
            rf = r.get('RowFlags')
            if isinstance(rf, list) and 1 in rf:
                continue
            j = r.get('AutoSelectJoken') or {}
            v = j.get('VarName')
            if not v:
                continue
            # 出所の有無は問わず、最終値(未確定は 0 扱い)が行を選択するかで判定。
            #   自由変数でも 0 が行を選ぶ場合 (例: 07 材料補正区分 ZHK=0→Row「数量に補正」)
            #   は製品側で自動確定し UI に出ない。0 がどの行も選ばなければ (例: 01 A3,
            #   02 RFG, 06 購入土 ZK) ユーザ選択質問として表示される。
            val = self._resolve_value(v)
            if val is None:
                val = 0.0
            if self._joken_selects(j, val):
                return True  # 最終値が行を自動選択 → 自動決定
        return False
    def _baseline_walk(self):
        if self._baseline_visited is None:
            walker = FlowWalker(self.new_json)
            result = walker.walk()
            self._baseline_visit_seq = result.get('visited_sitsumons', [])
            self._baseline_visited = set(self._baseline_visit_seq)
            self._baseline_row_sources = result.get('row_sources', {})
            # 自動決定判定用: baseline走査で解決された変数の集合
            try:
                self._baseline_scope = set(walker.hyo._user_inputs.keys())
                self._baseline_scope_dict = dict(walker.hyo._user_inputs)
            except Exception:
                self._baseline_scope = set((result.get('scope') or {}).keys())
                self._baseline_scope_dict = dict(result.get('scope') or {})
        return self._baseline_visited

    # ------------------------------------------------------------------
    # 経路探索 (single-probe)
    # ------------------------------------------------------------------

    def _find_path_to_target(self, target_sit_no):
        self._baseline_walk()
        seen = set()
        for sn in self._baseline_visit_seq:
            if sn in seen:
                continue
            seen.add(sn)
            sit019 = self.new_json.sitsumon019_by_no.get(sn)
            if not sit019:
                continue
            selectable = [
                r['RowID'] for r in sit019.get('SitRows', [])
                if r.get('Visible', True) and not r.get('IsFixed', False)
            ]
            if len(selectable) < 2:
                continue
            for row_id in selectable:
                walker = FlowWalker(self.new_json, vary_selections={sn: row_id})
                result = walker.walk()
                visited = result.get('visited_sitsumons', [])
                if target_sit_no in visited:
                    return (sn, row_id)
        return None

    # ------------------------------------------------------------------
    # 軸列挙 (絞り込み版)
    # ------------------------------------------------------------------

    def _collect_axes(self):
        baseline_visited = self._baseline_walk()
        sit_by_no = {s['SitsumonNo']: s for s in self.new_json.data.get('SitsumonItem', [])}

        vary_diff = self._vary_targets_from_diff()

        path_fix = {}
        vary_axes = []
        excluded_targets = []
        for sit_no, reasons in vary_diff.items():
            s = sit_by_no.get(sit_no)
            if not s:
                continue
            # 過去リリース由来の「質問追加」は今回の差分(vary)にしない → 既存質問としてfixへ
            if '新規追加質問' in reasons and self._is_stale_addition(s):
                reasons = [r for r in reasons if r != '新規追加質問']
                if not reasons:
                    continue
            if not self._is_ui_visible_axis(s):
                continue
            if sit_no in baseline_visited:
                vary_axes.append({'sit': s, 'reason': ' / '.join(dict.fromkeys(reasons))})
            else:
                path = self._find_path_to_target(sit_no)
                if path is None:
                    excluded_targets.append((sit_no, s.get('Mesho', '')))
                    continue
                probe_sn, probe_rid = path
                path_fix[probe_sn] = probe_rid
                vary_axes.append({'sit': s, 'reason': ' / '.join(dict.fromkeys(reasons))})

        result_axes = []
        vary_sit_nos = {ax['sit']['SitsumonNo'] for ax in vary_axes}
        for ax in vary_axes:
            s = ax['sit']
            # Fix A: 差分検出の vary 候補でも、J3「デフォルト実行」条件
            #   (ExecKind=1 + Flags⊆{105,108} + Kind≠17) に一致する質問は
            #   ユーザに問われずデフォルト選択で固定実行される → auto に降格。
            #   (例: 04 No:3 日当り施工量。選択肢追加が検出されても実体は単一デフォルト固定)
            if self._is_default_exec(s):
                result_axes.append({
                    'sit': s,
                    'kind': 'auto',
                    'reason': 'デフォルト実行 (SitsumonExecuteKind=1, SekisanEnv連動なし)',
                    'forced_row_id': '',
                })
            else:
                result_axes.append({
                    'sit': s,
                    'kind': 'vary',
                    'reason': ax['reason'],
                    'forced_row_id': '',
                })

        # 選択肢追加経由で新規到達する質問を軸へ昇格 (#21 内径又は内空幅(各種))
        #   ベースライン走査では到達しないため従来は軸に収集されなかった。
        #   diff vary 質問の各選択行を forward 試行し、新規 visited を拾う。
        #   (例: 21 側溝規格=各種(追加行) → Sit59(=Sit5のShortCut) が新規到達。
        #    J2=0 で自動選択が成立せずユーザが選ぶ質問になる)
        _probed = set()
        for ax in vary_axes:
            v_no = ax['sit']['SitsumonNo']
            s019p = self.new_json.sitsumon019_by_no.get(v_no)
            if not s019p or self.old_json is None:
                continue
            # 「追加された選択肢」の行だけ forward 試行する。既存選択肢経由で
            #   到達する質問は旧版でも到達可能=今回の差分ではない (#12 で
            #   クローラクレーン規格選択を誤昇格した教訓)。
            old_s019p = self.old_json.sitsumon019_by_no.get(v_no)
            old_rows_p = {r['RowID'] for r in (old_s019p.get('SitRows', [])
                                               if old_s019p else [])
                          if r.get('Visible', True) and not r.get('IsFixed', False)}
            for r in s019p.get('SitRows', []):
                if not r.get('Visible', True) or r.get('IsFixed', False):
                    continue
                if r['RowID'] in old_rows_p:
                    continue
                res_p = FlowWalker(self.new_json,
                                   vary_selections={v_no: r['RowID']}).walk()
                for sn_p in res_p.get('visited_sitsumons', []):
                    if (sn_p in baseline_visited or sn_p in vary_sit_nos
                            or sn_p in _probed):
                        continue
                    s2 = sit_by_no.get(sn_p)
                    if not s2 or not self._is_ui_visible_axis(s2):
                        continue
                    _probed.add(sn_p)
                    result_axes.append({
                        'sit': s2,
                        'kind': 'vary',
                        'reason': '追加選択肢経由の新規到達(全選択肢網羅)',
                        'forced_row_id': '',
                    })
        vary_sit_nos |= _probed

        seen = set(vary_sit_nos)
        for sn in self._baseline_visit_seq:
            if sn in seen:
                continue
            seen.add(sn)
            s = sit_by_no.get(sn)
            if not s:
                continue
            if not self._is_ui_visible_axis(s):
                continue
            src = self._baseline_row_sources.get(sn)
            forced = path_fix.get(sn, '')
            exec_kind = s.get('SitsumonExecuteKind')
            sit_flags = s.get('SitsumonFlags', []) or []
            # J3: 「デフォルト実行」 と「デフォルト初期値」 の区別
            #   - 105 = IsGaia9SitsumonItem, 108 = Gaia9関連 (Sirius テスト判明)
            #   - 1,2,3,100 等 = SekisanEnv 連動フラグ (積算環境設定で挙動が変わる)
            #     → JSON には SekisanEnv 値が無い → auto/user-select 未確定 → 安全側で fix
            extra_flags = [f for f in sit_flags if f not in (105, 108)]
            # 初期値変更 (step1: 質問設定/変更/備考=初期値変更) の質問は
            #   fix のまま新デフォルトを表示しつつ、変更理由として記録 → step3 で確認観点を出す
            default_changed = any(
                r.get('カテゴリ') == '質問設定' and r.get('備考') == '初期値変更'
                and self._parse_sitsumon_no(r.get('ID', '')) == sn
                for r in self.diff_rows
            )
            if forced:
                kind = 'fix'
                reason = '絞り込みで強制(vary到達経路)'
            elif self._level_value_is(s, 3):
                # レベル変数=3 (Execute/必ず実行) はユーザが選ぶ質問 → vary
                #   (仕様§1.5。src=='auto' でも auto に落とさない。例 12/15/17 クレーン賃料補正・子代価)
                kind = 'vary'
                reason = 'レベル変数=3(必ず実行)'
            elif src == 'auto' and exec_kind == 2 and not s.get('LevelVarName'):
                # 自動選択+ユーザー実行 → 通常はスキップ。ただしレベル変数を持つ質問は
                # レベル最終値≠1なら「開く」(ここに到達した時点で≠1) → fix列として表示する。
                continue
            elif src == 'auto':
                kind = 'auto'
                reason = '自動選択(AutoSelectJoken)'
            elif self._all_rows_autoselect(s):
                # B-15: 全選択可能行が条件付き = ユーザが直接選ばない質問。
                #   駆動変数(例 W)が未解決でも自動確定として列から除外する。
                kind = 'auto'
                reason = '自動確定 (全選択可能行がAutoSelectJoken付き・ユーザ選択なし)'
            elif self._is_default_exec(s) and s.get('LevelVarName'):
                # #21 FB: デフォルト実行 + レベル変数持ち (例: 側溝材料初期値区分
                #   LE_ST) は「単価選択の初期値を切り替えるだけの機能質問」。
                #   テスト変数ではないため列に出さない (自動確定扱い)。
                #   ※レベル変数=3(必ず実行)は上の分岐で vary 済み。
                kind = 'auto'
                reason = '自動確定 (デフォルト実行+レベル変数: 単価選択の機能質問)'
            elif self._is_default_exec(s):
                # J3: ExecKind=1 + Flags=[105]/[105,108]のみ + 数値入力でない = 純粋なデフォルト実行
                #   SitsumonKind=17 (数値入力) は MinKigou≥1 で UI 可視 → ユーザ入力 → fix
                kind = 'auto'
                reason = 'デフォルト実行 (SitsumonExecuteKind=1, SekisanEnv連動なし)'
            else:
                kind = 'fix'
                reason = ''
            if default_changed and '初期値変更' not in reason:
                reason = (reason + ' / ' if reason else '') + '初期値変更'
            result_axes.append({
                'sit': s,
                'kind': kind,
                'reason': reason,
                'forced_row_id': str(forced) if forced else '',
            })

        for probe_sn, probe_rid in path_fix.items():
            if probe_sn in seen:
                continue
            s = sit_by_no.get(probe_sn)
            if not s or not self._is_ui_visible_axis(s):
                continue
            result_axes.append({
                'sit': s,
                'kind': 'fix',
                'reason': '絞り込みで強制(vary到達経路)',
                'forced_row_id': str(probe_rid),
            })

        # ShortCut重複排除: 参照先(ShortCut先)も同じく軸になっている場合、
        #   参照側(ShortCut元)は同一質問を別ルートに置いた重複なので除外し1列に統合する。
        #   例: 03 Sit37(クーリングタワー延運転時間, ShortCut→Sit4) を除外し Sit4 の1列に。
        axis_nos = {ax['sit'].get('SitsumonNo') for ax in result_axes}
        deduped = []
        for ax in result_axes:
            sc = ax['sit'].get('ShortCutSitsumonNo')
            if sc and sc in axis_nos:
                continue  # 参照先も軸 → 重複 → 除外
            deduped.append(ax)
        result_axes = deduped

        self._apply_axis_behaviors(result_axes)
        self._excluded_targets = excluded_targets
        return result_axes

    def _apply_axis_behaviors(self, axes):
        behaviors = self.global_rules.get('axis_behaviors') or []
        if not behaviors:
            return
        has_keisan_change = any(
            r.get('カテゴリ') == '計算表' and r.get('変更種別') in ('変更', '追加', '削除')
            for r in self.diff_rows
        )
        for entry in behaviors:
            axis_name = entry.get('axis_name')
            condition = (entry.get('promote_to_vary_when') or {}).get('condition')
            if not axis_name:
                continue
            for ax in axes:
                if ax['sit'].get('Mesho') != axis_name:
                    continue
                if condition == 'change_affects_suryo_via_axis_switch' and has_keisan_change:
                    if ax['kind'] != 'vary':
                        ax['kind'] = 'vary'
                        ax['reason'] = f'業務ルール: {axis_name}の切替が代価表数量に影響'

    def generate(self):
        axes = self._collect_axes()

        order_map = {}
        for idx, sn in enumerate(self._baseline_visit_seq):
            if sn not in order_map:
                order_map[sn] = idx

        def sort_key(ax):
            sn = ax['sit'].get('SitsumonNo')
            return (order_map.get(sn, 10**9 + sn),)

        axes.sort(key=sort_key)

        plan = []
        for ax in axes:
            sit = ax['sit']
            no = sit.get('SitsumonNo')
            name = sit.get('Mesho', f'SitsumonNo:{no}')
            kind = ax['kind']
            reason = ax['reason']
            forced = ax['forced_row_id']
            label = f'{name}(固定)' if kind == 'auto' else name
            note = f'SitsumonKind={sit.get("SitsumonKind")}'
            plan.append([
                self._next_id(),
                name,
                kind,
                no,
                label,
                reason,
                note,
                forced,
            ])
        return plan


def run(diff_csv_path, new_json_path, output_path, old_json_path=None):
    gen = TestPlanGenerator(diff_csv_path, new_json_path, old_json_path)
    plan = gen.generate()
    BugakariJSON.write_csv([HEADER] + plan, output_path)
    vary_n = sum(1 for r in plan if r[2] == 'vary')
    auto_n = sum(1 for r in plan if r[2] == 'auto')
    fix_n = sum(1 for r in plan if r[2] == 'fix')
    forced_n = sum(1 for r in plan if r[7])
    print(f'テスト計画生成完了: {output_path}')
    print(f'  軸合計: {len(plan)}件 (vary={vary_n} / auto={auto_n} / fix={fix_n} / うち強制={forced_n})')
    for r in plan:
        if r[2] == 'vary':
            print(f'  [vary] {r[1]} ({r[5]})')
        elif r[7]:
            print(f'  [fix:強制 Row{r[7]}] {r[1]} ({r[5]})')
    excluded = getattr(gen, '_excluded_targets', [])
    if excluded:
        print(f'  到達経路なし(除外): {len(excluded)}件 → {", ".join(f"Sit{n}" for n, _ in excluded)}')


if __name__ == '__main__':
    if len(sys.argv) < 4:
        print('Usage: python generate_proposals.py <diff_csv> <new_json> <output_csv> [old_json]')
        sys.exit(1)
    old_json = sys.argv[4] if len(sys.argv) > 4 else None
    run(sys.argv[1], sys.argv[2], sys.argv[3], old_json)