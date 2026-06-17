"""
③ 列形式テストケースCSV生成 (絞り込みアプローチ対応)

【機能】
- 列順: フロー登場順 (baseline_walker の初回出現順)
- 強制行ID: step2.0 の「強制行ID」 列を読み、fix軸の指定行で固定 (vary到達経路)
- TC walker: 強制行+vary を flow_walker に渡し、訪問された Sit のみ列に残す
- display_col 改善 (I): unique 値が多い列を優先
- 任意入力表記 (B): SitsumonKind=17 は「任意」
- 期待値の自然言語化 (F): 任意入力軸を含む TC で値=0 のとき「計算結果が正しいか」
- テスト区分判定 (G):
   - 追加 Row より上の既存 Row は除外 (オーバーテスト防止)
   - 追加 Row を選ぶ TC = 差分、それ以外 = 回帰
   - 業務ルール vary 軸の「状態戻し回帰TC」 を1件追加

【入力】
- step2.0_テスト計画.csv
- 新JSON (および旧JSON: G 判定用)

【出力】
- step3.0_テストケース.csv
"""

import sys
import os
import csv
import re
import itertools

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'engine'))
from bugakari_json import BugakariJSON, KeisanHyo, ExternalReferenceError, ExpressionError
from flow_walker import FlowWalker


S_VARS = ['S1', 'S2', 'S3', 'S4', 'S5']


class ColumnTCGenerator:

    def __init__(self, plan_csv_path, new_json_path, old_json_path=None,
                 ref_json_path=None):
        self.new_json = BugakariJSON(new_json_path)
        self.old_json = BugakariJSON(old_json_path) if old_json_path else None
        # 参考JSON (input/参考/)。全パターン型で「文字のみの修正」の比較元に使う
        #   (#14: 複写元と比べて質問名/計算表名/選択肢テキストの文字修正観点を出す)。
        #   旧JSONがある差分型では使わない。
        self.ref_json = (BugakariJSON(ref_json_path)
                         if (ref_json_path and old_json_path is None) else None)
        self.plan = self._load_plan(plan_csv_path)
        self._added_row_index_by_sit = self._detect_added_rows() if self.old_json else {}
        self._intent_text = self._load_intent_text(new_json_path)

    def _detect_added_rows(self):
        result = {}
        for s_new in self.new_json.data.get('Sitsumon019', []):
            sno = s_new.get('SitsumonNo')
            new_selectable = [
                r['RowID'] for r in s_new.get('SitRows', [])
                if r.get('Visible', True) and not r.get('IsFixed', False)
            ]
            old_s = self.old_json.sitsumon019_by_no.get(sno)
            if not old_s:
                if new_selectable:
                    result[sno] = 0
                continue
            old_selectable = set(
                r['RowID'] for r in old_s.get('SitRows', [])
                if r.get('Visible', True) and not r.get('IsFixed', False)
            )
            for idx, rid in enumerate(new_selectable):
                if rid not in old_selectable:
                    result[sno] = idx
                    break
        return result

    def _load_intent_text(self, new_json_path):
        """修正方針.txt を読む (new_json と同じ input ディレクトリ。壊れバイトは置換)。"""
        import os
        if not new_json_path:
            return ''
        p = os.path.join(os.path.dirname(new_json_path), '修正方針.txt')
        try:
            with open(p, encoding='utf-8', errors='replace') as fh:
                return fh.read()
        except Exception:
            return ''

    def _intent_check_text(self, mesho):
        """修正方針の本文から、その軸(質問名)に対応する確認観点を生成する。
        固定文ではなく方針文から導く。例:
          『「X1:目地の種類」ごとに必要な資材の動作になるようフローを見直し』
          → 『・「X1:目地の種類」ごとに必要な資材の動作になっているか』
        指示文(〜になるよう〜見直し/〜してください/〜する 等)を検証形へ変換する。
        """
        import re
        text = getattr(self, '_intent_text', '') or ''
        if not text or not mesho:
            return None
        keywords = ('ごと', 'それぞれ', '種類別', '毎',
                    'フロー見直', 'フローを見直', '分岐', 'パターン')
        for raw in text.splitlines():
            line = raw.strip().lstrip('・*-•　 ')
            cmp = line.replace('「', '').replace('」', '')
            if mesho in cmp and any(k in cmp for k in keywords):
                idx = line.find('になるよう')
                if idx != -1:
                    return '・' + line[:idx] + 'になっているか'
                t = re.sub(r'(を)?(見直[しすせ]?|見直す|修正(する)?|対応(する)?|反映(する)?|してください|する)\s*$', '', line).rstrip('、。 ')
                return '・' + t + 'が修正方針どおりか'
        return None

    def _load_plan(self, path):
        with open(path, encoding='cp932', newline='') as f:
            return list(csv.DictReader(f))

    def _get_axis_rows(self, sitsumon_no):
        sit019 = self.new_json.sitsumon019_by_no.get(sitsumon_no)
        if sit019 is None:
            # J6: ShortCut元 Sit は Sitsumon019 データを持たないことがある。
            #     SitsumonItem.ShortCutSitsumonNo を見て、ShortCut先 Sit019 を参照する。
            for s in self.new_json.data.get('SitsumonItem', []):
                if s.get('SitsumonNo') == sitsumon_no and s.get('ShortCutSitsumonNo'):
                    sc_no = s['ShortCutSitsumonNo']
                    sit019 = self.new_json.sitsumon019_by_no.get(sc_no)
                    if sit019:
                        break
            if sit019 is None:
                return self._handle_non_019(sitsumon_no)
        cells = {
            (c['RowID'], c['ColID']): c.get('Value', '')
            for c in sit019.get('SitTabCells', [])
        }
        cols = sit019.get('SitCols', [])
        var_cols = [c for c in cols if c.get('VarName')]
        sit_rows = sit019.get('SitRows', [])
        selectable_row_ids = [
            r['RowID'] for r in sit_rows
            if r.get('Visible', True) and not r.get('IsFixed', False)
        ]
        # D 改善: VarName 持ち列も表示候補に入れる (規格コード等が業務的に重要)
        #   I (unique_count 優先) で適切な列が選ばれる
        header_row_ids = [
            r['RowID'] for r in sit_rows
            if r.get('Visible', True) and r.get('IsFixed', False)
        ]

        def _header_text(col_id):
            for hr in header_row_ids:
                v = cells.get((hr, col_id))
                if v:
                    return str(v)
            return ''

        disp_col_candidates = [
            c for c in cols
            if c.get('Visible', True)
            # 「係数」列 (補正係数 1/1.1 等) は計算パラメータであり名称ではない → 表示候補から除外
            and '係数' not in _header_text(c.get('ColID'))
        ]
        if not disp_col_candidates:
            disp_col_candidates = [c for c in cols if c.get('Visible', True)]
        disp_col = None
        best_score = (-1, -1, -1)
        for c in disp_col_candidates:
            col_id = c.get('ColID')
            values = []
            for rid in selectable_row_ids:
                v = cells.get((rid, col_id))
                if v and str(v).strip():
                    values.append(str(v).strip())
            text_count = len(values)
            unique_count = len(set(values))
            # D 改善: 同点なら VarName 持ち列 (規格コード等) を優先
            has_varname = 1 if c.get('VarName') else 0
            score = (unique_count, has_varname, text_count)
            if score > best_score:
                best_score = score
                disp_col = col_id

        # J4: 「規格コード」 列の併記
        #   VarName 持ち列に -9999999999 (業務的に「未対策」 等を表す記号値) を
        #   含む列があれば、その数値を併記して「1300 (排ガス 2014年規制)」 形式に。
        #   02 Sit 82 ピンポイントで「1300」 等の規格コード表示が欲しいケースに対応。
        value_col = None
        for c in cols:
            if not c.get('Visible', True):
                continue
            if not c.get('VarName'):
                continue
            cid = c.get('ColID')
            if cid == disp_col:
                continue  # 既に表示列として採用された列は除外
            vals = [str(cells.get((rid, cid), '')).strip() for rid in selectable_row_ids]
            if '-9999999999' in vals:
                value_col = cid
                break
        result = []
        for sr in sit_rows:
            row_id = sr.get('RowID')
            if not sr.get('Visible', True) or sr.get('IsFixed', False):
                continue
            text_display = self._strip_ref_code(cells.get((row_id, disp_col), '').replace('\r\n', ' ').strip()) if disp_col else f'Row{row_id}'
            # J4: 規格コード列の併記
            if value_col is not None:
                value_part = str(cells.get((row_id, value_col), '')).strip()
                if value_part and value_part != '-9999999999':
                    display = f'{value_part} ({text_display})' if text_display else value_part
                else:
                    display = text_display  # -9999999999 や空のときは値併記しない
            else:
                display = text_display
            var_settings = {}
            for vc in var_cols:
                val = cells.get((row_id, vc['ColID']))
                if val is not None and val != '':
                    var_settings[vc['VarName']] = val
            result.append({
                'row_id': row_id,
                'display': display or f'Row{row_id}',
                'var_settings': var_settings,
                # Fix B: タブ対応の表示再解決に使うメタ情報
                'sit_no': sitsumon_no,
                'disp_col': disp_col,
                'value_col': value_col,
            })
        return result

    # タブセルの選択肢文字列には編集用の参照コード注記 (例「【A=1】\r\n土留無」)
    # が前置されることがある。これは内部メタ情報であり選択肢名ではないため除去する。
    #   - 【…】 で始まり、直後に改行/空白が続くものを 1 個だけ剥がす。
    #   - 注記の後ろに実体ラベル (土留無 等) が残る場合のみ剥がす (注記単独なら維持)。
    _REF_CODE_RE = re.compile(r'^【[^】]*】[\s　]+(?=\S)')

    @classmethod
    def _strip_ref_code(cls, text):
        if not text:
            return text
        return cls._REF_CODE_RE.sub('', str(text))

    def _option_texts(self, bj, sit_no):
        """質問の選択肢表示テキスト集合 (タブなしセル基準)。"""
        s019 = bj.sitsumon019_by_no.get(sit_no)
        if not s019:
            for s in bj.data.get('SitsumonItem', []):
                if s.get('SitsumonNo') == sit_no and s.get('ShortCutSitsumonNo'):
                    s019 = bj.sitsumon019_by_no.get(s['ShortCutSitsumonNo'])
                    break
        if not s019:
            return set()
        cells = {(c['RowID'], c['ColID']): c.get('Value', '')
                 for c in s019.get('SitTabCells', []) if not c.get('TabNo')}
        rows = [r['RowID'] for r in s019.get('SitRows', [])
                if r.get('Visible', True) and not r.get('IsFixed', False)]
        cols = [c.get('ColID') for c in s019.get('SitCols', [])
                if c.get('Visible', True)]
        out = set()
        for rid in rows:
            for cid in cols:
                v = str(cells.get((rid, cid), '') or '').replace('\r\n', ' ').strip()
                if v:
                    out.add(v)
                    break
        return out

    def _deleted_options(self, sit_no):
        """旧JSONにあって新JSONにない選択肢 (選択肢削除の確認観点用)。"""
        if self.old_json is None:
            return []
        return sorted(self._option_texts(self.old_json, sit_no)
                      - self._option_texts(self.new_json, sit_no))

    def _display_for_tab(self, row, hyo):
        """Fix B: 複数タブ質問は、TCの変数スコープ(hyo)で有効タブを決め、
        そのタブのセル値で表示を再構築する。単一タブ質問は従来の display を返す。
        """
        if not row:
            return ''
        sit_no = row.get('sit_no')
        disp_col = row.get('disp_col')
        if sit_no is None:
            return row['display']
        tabs = self.new_json.tabs_for(sit_no)
        if len(tabs) <= 1:
            return row['display']  # 単一/タブなし → 従来通り (出力不変)
        active_tab = self.new_json.active_tab_no(sit_no, hyo)
        sit019 = self.new_json.sitsumon019_by_no.get(sit_no)
        if not sit019 or disp_col is None:
            return row['display']
        rid = row['row_id']
        text = self.new_json.cell_value_for_tab(sit019, rid, disp_col, active_tab)
        text_display = self._strip_ref_code(str(text).replace('\r\n', ' ').strip()) if text else ''
        value_col = row.get('value_col')
        if value_col is not None:
            vp = self.new_json.cell_value_for_tab(sit019, rid, value_col, active_tab)
            vp = str(vp).strip() if vp is not None else ''
            if vp and vp != '-9999999999':
                return f'{vp} ({text_display})' if text_display else vp
        return text_display or row['display']

    def _handle_non_019(self, sitsumon_no):
        # Kind=8 (単価マスタ選択): テスターがマスタから選ぶ → 「任意」表示 (例: 06 材料の選択=土のう種類)
        sit = self.new_json.sitsumon_by_no.get(sitsumon_no, {})
        if sit.get('SitsumonKind') == 8:
            return [{'row_id': 0, 'display': '任意', 'var_settings': {},
                     'sit_no': sitsumon_no, 'disp_col': None, 'value_col': None}]
        for s017 in self.new_json.data.get('Sitsumon017', []):
            if s017.get('SitsumonNo') == sitsumon_no:
                default = s017.get('DefaultValue', 0)
                vname = s017.get('VarName', '')
                return [{
                    'row_id': 0,
                    'display': '任意',
                    'var_settings': {vname: default} if vname else {},
                }]
        return [{'row_id': 0, 'display': '(値なし)', 'var_settings': {}}]

    def _has_added_daika(self):
        """旧→新で代価表行が追加されたか (extract_diff と同一判定)。
        - 新規 DaikaItemCD (旧に無い) がある、または
        - 既存 DaikaItemCD の DaikaItemLine 行数が増えた
        old_json が無い場合は False。件数は CD 振り直しで不正確になるため出さない。
        """
        if getattr(self, '_added_daika_cache', None) is not None:
            return self._added_daika_cache
        if self.old_json is None:
            self._added_daika_cache = False
            return False
        def line_counts(data):
            c = {}
            for l in data.get('DaikaItemLine', []):
                cd = l.get('DaikaItemCD')
                if cd is not None:
                    c[cd] = c.get(cd, 0) + 1
            return c
        old_items = {d['DaikaItemCD'] for d in self.old_json.data.get('DaikaItem', [])}
        new_lc = line_counts(self.new_json.data)
        old_lc = line_counts(self.old_json.data)
        added = False
        for d in self.new_json.data.get('DaikaItem', []):
            cd = d.get('DaikaItemCD')
            if cd not in old_items:
                added = True
                break
            if new_lc.get(cd, 0) > old_lc.get(cd, 0):
                added = True
                break
        self._added_daika_cache = added
        return added

    def _has_kikaku_keijo(self, sitsumon_no):
        """J2: 規格名計上(規格名/規格を代価表へ計上)が設定された質問か。
        Gaia9方式(主): KikakuKeijoGaia9 レコードに該当 SitsumonNo があれば計上あり。
          (Sirius: Sitsumon019KikakuKeijoGaia9Test.cs。BugakariKanri.IsKikakuKeijoGaia9 工種)
        旧方式(フォールバック): SitTabCols.KikakuKeijoNaiyo != 0。
        """
        for r in self.new_json.data.get('KikakuKeijoGaia9', []) or []:
            if r.get('SitsumonNo') == sitsumon_no:
                return True
        # ShortCut元の質問は ShortCut先の規格名計上設定を引き継ぐ (例: 21 Sit59→Sit5)
        for s in self.new_json.data.get('SitsumonItem', []):
            if (s.get('SitsumonNo') == sitsumon_no
                    and s.get('ShortCutSitsumonNo')
                    and s.get('ShortCutSitsumonNo') != sitsumon_no):
                return self._has_kikaku_keijo(s['ShortCutSitsumonNo'])
        sit019 = self.new_json.sitsumon019_by_no.get(sitsumon_no)
        if not sit019:
            return False
        for col in sit019.get('SitTabCols', []):
            keijo = col.get('KikakuKeijoNaiyo')
            if keijo and keijo != 0:
                return True
        return False

    def _ref_comparable(self):
        """参考JSON(input/参考/)が「複写元」とみなせるか。
        #14 のように複写して文字修正した工種だけ文字比較する。
        #10 のように単なる設計参考(別工種)は構造が異なるため比較しない。
        判定: 質問No集合・計算表CD集合の Jaccard 類似度がともに 0.9 以上。"""
        if getattr(self, '_ref_comparable_cache', None) is not None:
            return self._ref_comparable_cache
        ok = False
        if self.ref_json is not None:
            # ID(質問No/計算表CD)は連番のため別工種でも一致してしまう。
            # 「JSON再利用(複写/リネーム)」の判定は内容ベース:
            #   ①質問No集合の Jaccard 類似度 0.8 以上 かつ
            #   ②同一質問Noの表示名(Mesho)完全一致率 0.5 以上
            #   (複写+文字修正なら大半の質問名は不変。別工種なら名前は揃わない)
            rm = {s['SitsumonNo']: (s.get('Mesho') or '').strip()
                  for s in self.ref_json.data.get('SitsumonItem', [])}
            nm = {s['SitsumonNo']: (s.get('Mesho') or '').strip()
                  for s in self.new_json.data.get('SitsumonItem', [])}
            common = set(rm) & set(nm)
            jac = len(common) / max(1, len(set(rm) | set(nm)))
            named = [no for no in common if rm[no] or nm[no]]
            same = sum(1 for no in named if rm[no] == nm[no])
            name_ratio = same / max(1, len(named))
            ok = jac >= 0.8 and name_ratio >= 0.5
        self._ref_comparable_cache = ok
        return ok

    def _name_change_checks(self):
        """文字のみの修正(質問表示名/計算表名称)の確認観点 (#13/#14)。
        比較元: 差分型=旧JSON / 全パターン型=参考JSON(input/参考/)。
        計算表名称(KeisanItem.Mesho)は代価表の行名称として表示されるため、
        「代価表名称や規格名の文字修正」の検証対象。TC は増やさず観点のみ追記。"""
        if getattr(self, '_name_change_cache', None) is not None:
            return self._name_change_cache
        base = self.old_json or (self.ref_json if self._ref_comparable() else None)
        out = []
        if base is not None:
            import difflib

            def _is_noise_name(nm):
                # 変数式名(NFG1=1 / 0.4=代価表… 等)・終点マーカーは UI 名称でない
                return (not nm) or ('=' in nm) or ('終点' in nm)

            def _row_ids(bj, no):
                s019 = bj.sitsumon019_by_no.get(no)
                if not s019:
                    return None
                return frozenset(r['RowID'] for r in s019.get('SitRows', [])
                                 if r.get('Visible', True)
                                 and not r.get('IsFixed', False))

            om = {s['SitsumonNo']: s for s in base.data.get('SitsumonItem', [])}
            for s in self.new_json.data.get('SitsumonItem', []):
                no = s['SitsumonNo']
                o = om.get(no)
                if not o:
                    continue
                nm = (s.get('Mesho') or '').strip()
                onm_ = (o.get('Mesho') or '').strip()
                if onm_ == nm or _is_noise_name(nm) or _is_noise_name(onm_):
                    continue
                # 番号ずれ(同じNoに別質問が来た)ガード:
                #   同一Kind(UI質問 17/19 のみ) + Kind19は選択可能RowID集合一致 +
                #   名称類似度 0.5 以上 (文字修正なら大部分が一致するはず)
                if s.get('SitsumonKind') not in (17, 19):
                    continue
                if o.get('SitsumonKind') != s.get('SitsumonKind'):
                    continue
                if s.get('SitsumonKind') == 19 and                         _row_ids(base, no) != _row_ids(self.new_json, no):
                    continue
                if difflib.SequenceMatcher(None, onm_, nm).ratio() < 0.5:
                    continue
                out.append((no, f'・質問名「{nm}」と表示されているが、'
                                f'外部設計と正しいか(文字修正)'))
            # 代価表で積算者が見るのは「代価名」と「備考欄」のみ (#13 FB)。
            #   計算表(KeisanItem)の名称は積算実行時に見えないため観点に出さない。
            # (1) 代価名 (Daika.Mesho / DaikaTitle) の文字変更
            odk = {d.get('DaikaHyoCD'): d for d in base.data.get('Daika', [])}
            for d in self.new_json.data.get('Daika', []):
                o = odk.get(d.get('DaikaHyoCD'))
                if not o:
                    continue
                for fld in ('Mesho', 'DaikaTitle'):
                    onm = (o.get(fld) or '').strip()
                    nnm = (d.get(fld) or '').strip()
                    if onm and nnm and onm != nnm:
                        out.append((None, f'・代価名「{nnm}」と表示されているが、'
                                          f'外部設計と正しいか(文字修正)'))
                        break  # 同一代価表で Mesho/Title 両方は出さない
                # 当り単位 (AtariTani) の変更 (#20 ため池堤体盛立工 m2→m3)。
                #   同名代価表のみ比較 (番号ずれ防止。step1 と同基準)
                if (o.get('Mesho') or '') == (d.get('Mesho') or ''):
                    oa = (o.get('AtariTani') or '').strip()
                    na = (d.get('AtariTani') or '').strip()
                    if oa and na and oa != na:
                        out.append((None, f'・代価表「{(d.get("Mesho") or "").strip()}」'
                                          f'の当り単位「{na}」が外部設計と正しいか'
                                          f'(単位変更。旧「{oa}」)'))
            # (2) 代価表の備考欄 (DaikaItemLine.Biko) の文字変更
            #   行の対応付け: 同一 DaikaItemCD 内の登場順。行数が同じ かつ
            #   単価リンク(TankaLinkKeisanItemCD)・列(Column)が同じ行のみ比較
            #   (行追加/削除のあった代価表は番号ずれするため対象外)。
            def _lines_by_item(bj):
                m = {}
                for l in bj.data.get('DaikaItemLine', []):
                    m.setdefault(l.get('DaikaItemCD'), []).append(l)
                return m
            olm, nlm = _lines_by_item(base), _lines_by_item(self.new_json)
            seen_biko = set()
            for cd, nls in nlm.items():
                ols = olm.get(cd)
                if not ols or len(ols) != len(nls):
                    continue
                for ol, nl in zip(ols, nls):
                    if (ol.get('TankaLinkKeisanItemCD') != nl.get('TankaLinkKeisanItemCD')
                            or ol.get('Column') != nl.get('Column')):
                        continue
                    ob = (ol.get('Biko') or '').strip()
                    nb = (nl.get('Biko') or '').strip()
                    if ob and nb and ob != nb and nb not in seen_biko:
                        seen_biko.add(nb)
                        out.append((None, f'・代価表の備考欄「{nb}」が外部設計と'
                                          f'正しいか(文字修正)'))
        self._name_change_cache = out
        return out

    def _child_daika_checks(self):
        """新提案B (#15 FB): 子代価(Sitsumon011)へ送る変数の増減・計上先変更を
        差分検知し、「子代価選択肢が意図通りか」の確認観点を出す。
        例: 15 質問No50 鉄筋工 F2削除/F7追加・計上先 28968→20301。"""
        if getattr(self, '_child_daika_cache', None) is not None:
            return self._child_daika_cache
        base = self.old_json or (self.ref_json if self._ref_comparable() else None)
        out = []
        if base is not None:
            om = {e['SitsumonNo']: e for e in base.data.get('Sitsumon011', [])}

            def send_vars(e):
                return [s.get('SendVarName') or s.get('CurVarName')
                        for s in (e.get('SendItemList') or [])]
            for n in self.new_json.data.get('Sitsumon011', []):
                no = n['SitsumonNo']
                o = om.get(no)
                if not o:
                    continue
                ov, nv = send_vars(o), send_vars(n)
                added = [v for v in nv if v not in ov]
                removed = [v for v in ov if v not in nv]
                bits = []
                if added:
                    bits.append('追加:' + ','.join(added))
                if removed:
                    bits.append('削除:' + ','.join(removed))
                keijo = (str(o.get('KeijoSakiTankaCD') or '')
                         != str(n.get('KeijoSakiTankaCD') or ''))
                if bits or keijo:
                    nm = self.new_json.get_sitsumon_name(no)
                    detail = ('送り変数の変更 ' + ' '.join(bits)) if bits else ''
                    if keijo:
                        detail = (detail + ' / ' if detail else '') + '計上先変更'
                    out.append((no, f'・子代価「{nm}」の選択肢が意図通りか({detail})'))
        self._child_daika_cache = out
        return out

    def _text_changed_sits(self):
        """選択肢テキスト変更(共通RowIDのセル文字列が変わった)質問Noの集合。
        step1 (extract_diff._diff_sitsumon019) と同基準。軸に上がらない自動確定
        質問(例: 12 機械区分71=機械質量区分のエコー)でも、規格名計上を持つなら
        確認観点を出すために step3 側でも検出する (フィードバック 2026-06-10)。"""
        if getattr(self, '_text_changed_cache', None) is not None:
            return self._text_changed_cache
        result = set()
        _base = self.old_json or (self.ref_json if self._ref_comparable() else None)
        if _base is not None:
            old_map = {s['SitsumonNo']: s
                       for s in _base.data.get('Sitsumon019', [])}
            for s in self.new_json.data.get('Sitsumon019', []):
                no = s['SitsumonNo']
                o = old_map.get(no)
                if not o:
                    continue
                oc = {(x['RowID'], x['ColID']): str(x.get('Value') or '')
                      for x in o.get('SitTabCells', [])}
                nc = {(x['RowID'], x['ColID']): str(x.get('Value') or '')
                      for x in s.get('SitTabCells', [])}
                common = ({r['RowID'] for r in o.get('SitTabRows', [])}
                          & {r['RowID'] for r in s.get('SitTabRows', [])})
                cols = sorted({cid for _, cid in oc} | {cid for _, cid in nc})

                def _textual(v):
                    import re as _re
                    sv = str(v).strip()
                    return bool(sv) and not _re.fullmatch(
                        r'[-+0-9.,eE%~\s　()]*', sv)
                found = False
                for rid in sorted(common):
                    for cl in cols:
                        a = oc.get((rid, cl), '')
                        b = nc.get((rid, cl), '')
                        if a != b and (_textual(a) or _textual(b)):
                            found = True
                            break
                    if found:
                        break
                if found:
                    result.add(no)
        self._text_changed_cache = result
        return result

    def _get_default_row(self, sitsumon_no, rows):
        for tab in self.new_json.data.get('SitTab', []):
            if tab.get('SitsumonNo') == sitsumon_no:
                default_id = tab.get('DefaultRowID')
                if default_id:
                    for r in rows:
                        if r['row_id'] == default_id:
                            return r
                break
        return rows[0] if rows else None

    def _get_row_by_id(self, rows, row_id):
        for r in rows:
            if r['row_id'] == row_id:
                return r
        return None

    def _daika_output_s(self):
        """期待値列に出す S 変数を「代価表に上がるもの」だけに厳選する。
        判定: Column を持つ DaikaItemLine が SuryoRitsuLinkKeisanItemCD で参照する
        KeisanItem(D*R 等)の式に(再帰的に)現れる S 変数。代価表行の登場順で重複排除。
        Column=None の行(代価表の列に乗らない=非出力)は対象外。
        """
        import re
        by_var = self.new_json.keisan_by_varname
        cd2var = {k.get('KeisanItemCD'): k.get('VarName')
                  for k in self.new_json.data.get('KeisanItem', [])}

        def s_in(v, seen):
            if not v or v in seen:
                return []
            seen.add(v)
            out = []
            if re.fullmatch(r'S\d+', v):
                out.append(v)
            k = by_var.get(v)
            if k and k.get('Expression'):
                for nv in re.findall(r"[A-Za-z~][A-Za-z0-9~_]*", k['Expression']):
                    out += s_in(nv, seen)
            return out

        ordered = []
        any_link = False
        for l in self.new_json.data.get('DaikaItemLine', []):
            v = cd2var.get(l.get('SuryoRitsuLinkKeisanItemCD'))
            if v:
                any_link = True
            if not v:
                continue
            # 数量率リンク(SuryoRitsuLink)と代価(DaikaItemCD)を持つ行は代価表に計上される
            #   出力行 → 期待値対象。Column の有無では除外しない
            #   (#28: D1R〜D3R は Column=None でも計上される。数量@計算で S1〜S3 が出る)。
            if l.get('DaikaItemCD') is None:
                continue
            for sv in s_in(v, set()):
                if sv not in ordered:
                    ordered.append(sv)
        if not any_link:
            # フォールバック: 代価表行がリンク(SuryoRitsuLinkKeisanItemCD)を一切持たない
            # 構造(例: 00 裏込砕石工)では、KeisanItem に定義(Value/Expression)を持つ Sn を
            # 番号順に出力対象とする。定義の無い空 S (例: 02 の S2) は出さない。
            cands = []
            for k in self.new_json.data.get('KeisanItem', []):
                v = k.get('VarName')
                if v and re.fullmatch(r'S\d+', v) and (k.get('Value') is not None or k.get('Expression')):
                    cands.append(v)
            ordered = sorted(set(cands), key=lambda x: int(x[1:]))
        return ordered

    @staticmethod
    def _is_noise_column(ax):
        """列に出さない軸か。
        除外するのは次のみ (テスト変数でなく、ユーザが再選択しないもの):
          (1) 「自動確定」軸 = AutoSelectJoken の駆動変数が確定し行が一意に決まる
              (例: 17 クレーン規格←L~CK=60)。再選択の余地がない帰結。
          (2) 「変数=値」形式の定数設定fix軸 (計設定。例 L~CK=60 / L~N=0 / L~α=1)。
        ※「デフォルト実行」「自動選択(AutoSelectJoken)」軸は **初回は選択不要だが
          ユーザが再選択可能** なので列に残す (02/03/04/06/07 等)。
        ※内部の確認観点(規格名計上・初期値変更等)では axes_displayed を引き続き使う。
        """
        if '自動確定' in (ax.get('変更理由', '') or ''):
            return True
        if ax.get('種別') == 'fix':
            import re as _re
            nm = (ax.get('軸名', '') or '').strip()
            if _re.fullmatch(r'[^\s=]+\s*=\s*-?\d+(?:\.\d+)?', nm):
                return True
        return False

    def _build_headers(self, axes_sorted):
        cols = ['テストID', 'テスト区分']
        cols += [ax['列ラベル'] for ax in axes_sorted]
        s_present = self._daika_output_s()
        cols += [f'期待:{v}' for v in s_present]
        cols += ['選択肢の適切さ確認', '規格名計上']
        return cols, s_present

    def _flow_equiv_rows(self, sit_no, rows):
        """選択肢削除系 vary 軸の行を到達フロー(visited 質問集合)で集約する。
        同一フローに落ちる行(値だけ違う。例 鉄筋径)は代表1件に圧縮し、フローが
        分岐する行(例 作業内容の撤去)は各代表を残す。直積爆発を防ぎ分岐網羅は保つ。
        """
        default = self._get_default_row(sit_no, rows)
        reps = []
        key_to_idx = {}
        for r in rows:
            res = FlowWalker(self.new_json, vary_selections={sit_no: r['row_id']}).walk()
            key = frozenset(res.get('visited_sitsumons', []))
            if key not in key_to_idx:
                key_to_idx[key] = len(reps)
                reps.append(r)
            elif default is not None and r['row_id'] == default['row_id']:
                # B-16: 既定行(DefaultRowID)が属するフロー類の代表は既定行にする。
                #   実機の既定選択と一致させる (例: 16 排ガス機械の選択 =
                #   第3次基準値が既定なのに先頭行の未対策型が代表になっていた)。
                reps[key_to_idx[key]] = r
        return reps if reps else (rows[:1] if rows else [])

    def _reach_chain(self, target_sit, base_forced, gate_sits, max_depth=4, budget=2500):
        """target_sit を visited にする選択チェーンを前方探索で求める (multi-step)。
        ゲート候補は gate_sits (= vary 軸の SitsumonNo 集合) に限定する。
        これにより非vary軸を forced_rows にグローバル適用する副作用を排除する。
        新質問を開く選択のみ再帰(貪欲)。多段ゲート対応
        (例: 桁区分=床版桁 → 埋設型枠計上=する → 使用数量)。
        見つかれば base_forced からの追加選択 dict を返す。無ければ None。"""
        calls = [0]
        def visited(sels):
            calls[0] += 1
            return set(FlowWalker(self.new_json, vary_selections=sels)
                       .walk().get('visited_sitsumons', []))
        found = {}
        def dfs(sels, vis, depth, seen):
            if target_sit in vis:
                found.clear(); found.update(sels); return True
            if depth <= 0 or calls[0] > budget:
                return False
            for sn in sorted(vis):
                if sn in sels or sn not in gate_sits:
                    continue
                sit = self.new_json.sitsumon_by_no.get(sn)
                if not sit or sit.get('SitsumonKind') != 19:
                    continue
                s019 = self.new_json.sitsumon019_by_no.get(sn)
                if not s019:
                    continue
                rws = [r['RowID'] for r in s019.get('SitRows', [])
                       if r.get('Visible', True) and not r.get('IsFixed', False)]
                if len(rws) < 2:
                    continue
                for rid in rws:
                    if calls[0] > budget:
                        return False
                    ns = dict(sels); ns[sn] = rid
                    key = tuple(sorted(ns.items()))
                    if key in seen:
                        continue
                    seen.add(key)
                    nv = visited(ns)
                    if target_sit in nv or (nv - vis):
                        if dfs(ns, nv, depth - 1, seen):
                            return True
            return False
        base_vis = visited(dict(base_forced))
        if dfs(dict(base_forced), base_vis, max_depth, set()):
            return {k: v for k, v in found.items() if base_forced.get(k) != v}
        return None

    def _compute_reach_combos(self, vary_row_lists, forced_rows):
        """到達しない vary 軸のうち『修正方針が言及している軸』だけを対象に、
        到達チェーン(ゲートは vary 軸に限定)を求め、到達用 combo の override を返す。
        副作用を持たない: forced_rows は変更しない / 既存 combo は触らない(純加算)。
        戻り値: (default_rows, [override_dict, ...])。override は {vary軸index: row}。"""
        if not vary_row_lists:
            return [], []
        axis_sit = [int(ax['SitsumonNo']) for ax, _ in vary_row_lists]
        gate_sits = set(axis_sit)
        default_rows = [rs[0] for _, rs in vary_row_lists]
        intent = getattr(self, '_intent_text', '') or ''
        def referenced(mesho):
            # 方針本文と質問名が長さ>=4の部分文字列を共有するか(=方針が触れている)
            m = (mesho or '')
            for i in range(len(m) - 3):
                if m[i:i+4] in intent:
                    return True
            return False
        def base_visited():
            sels = dict(forced_rows)
            for s_, r in zip(axis_sit, default_rows):
                sels[s_] = r['row_id']
            return set(FlowWalker(self.new_json, vary_selections=sels)
                       .walk().get('visited_sitsumons', []))
        bvis = base_visited()
        specs = []
        for i, (ax, rs) in enumerate(vary_row_lists):
            sit = axis_sit[i]
            if sit in bvis:
                continue
            if not referenced(ax.get('軸名') or ''):
                continue  # 方針が言及していない軸は到達させない(他工種への波及防止)
            chain = self._reach_chain(sit, forced_rows, gate_sits)
            if not chain:
                continue
            sels = dict(forced_rows)
            for gsn, grid in chain.items():
                sels[gsn] = grid   # ゲートは vary 軸のみ → combo override で表現
            sels[sit] = rs[0]['row_id']
            res = FlowWalker(self.new_json, vary_selections=sels).walk()
            if sit not in set(res.get('visited_sitsumons', [])):
                continue
            sit_sel = res.get('sit_selections', {})
            ov = {}
            for j, sj in enumerate(axis_sit):
                rid = sit_sel.get(sj)
                if rid is None:
                    continue
                rowobj = self._get_row_by_id(self._get_axis_rows(sj), rid)
                if rowobj is not None:
                    ov[j] = rowobj
            ov[i] = rs[0]
            specs.append(ov)
        if specs:
            print(f'  [multi-step到達] {len(specs)}件の到達用comboを追加(方針言及軸)')
        return default_rows, specs


    def generate(self):
        vary_axes = [ax for ax in self.plan if ax['種別'] == 'vary']
        fix_or_auto_axes = [ax for ax in self.plan if ax['種別'] in ('fix', 'auto')]

        # 強制行ID (列順決定の前に取得)
        forced_rows = {}
        for ax in self.plan:
            forced = ax.get('強制行ID', '')
            if forced:
                try:
                    forced_rows[int(ax['SitsumonNo'])] = int(forced)
                except ValueError:
                    pass
        if forced_rows:
            print(f'  [強制行] {len(forced_rows)} 件: '
                  + ', '.join(f'Sit{k}=R{v}' for k, v in forced_rows.items()))

        # J5: 列順は「強制行込み」 のフロー走査で決定する
        #   baseline (vary無し+強制行無し) では Sit 11=800kg経路で Sit 82 が
        #   訪問されず末尾に追いやられる。
        #   強制行込みの走査なら Sit 11=1300kg → Sit 82 が直後に来る正しい順。
        _for_order_walker = FlowWalker(self.new_json, vary_selections=dict(forced_rows))
        _for_order_result = _for_order_walker.walk()
        _visit_seq = _for_order_result.get('visited_sitsumons', [])
        _visit_order = {}
        for idx, sn in enumerate(_visit_seq):
            if sn not in _visit_order:
                _visit_order[sn] = idx

        # 追加選択肢経由で新規到達する軸の列順 (#21 FB: 内径又は内空幅は
        #   基礎砕石の有無の直前が正)。既定順走査では未訪問のため末尾に落ちる。
        #   トリガー(追加選択肢)込みの走査列を使い、直前の既知質問の直後に置く。
        _unplaced = [int(ax['SitsumonNo']) for ax in self.plan
                     if '新規到達' in (ax.get('変更理由') or '')
                     and int(ax['SitsumonNo']) not in _visit_order]
        if _unplaced:
            for _v_no, _a_idx in (self._added_row_index_by_sit or {}).items():
                if not _unplaced:
                    break
                _rows_v = self._get_axis_rows(_v_no)
                if not isinstance(_rows_v, list):
                    continue
                for _r in _rows_v[_a_idx:]:
                    _seq2 = FlowWalker(
                        self.new_json,
                        vary_selections={_v_no: _r['row_id']},
                    ).walk().get('visited_sitsumons', [])
                    for _s_no in list(_unplaced):
                        if _s_no not in _seq2:
                            continue
                        _pos = _seq2.index(_s_no)
                        for _prev in reversed(_seq2[:_pos]):
                            if _prev in _visit_order:
                                _visit_order[_s_no] = _visit_order[_prev] + 0.5
                                _unplaced.remove(_s_no)
                                break

        # multi-step到達でのみ到達する vary 軸の列順 (#27 埋設型枠系)。
        #   スコープは _compute_reach_combos と同一: 「修正方針が言及している軸」かつ
        #   「既定combo(forced+各軸既定行)で未到達」のみ。これにより通常の直積で到達する
        #   軸(例 #22 振動ローラ=混合深さdeep。方針は半角ﾛｰﾗでMesho全角ローラと不一致)は
        #   対象外となり、既存工種の列順を変えない。
        _vary_sits_for_order = {int(ax['SitsumonNo']) for ax in self.plan
                                if ax.get('種別') == 'vary'}
        _intent_o = getattr(self, '_intent_text', '') or ''
        def _ref_o(m):
            m = m or ''
            return any(m[i:i+4] in _intent_o for i in range(len(m) - 3))
        _base_def_o = set(FlowWalker(self.new_json, vary_selections=dict(forced_rows))
                          .walk().get('visited_sitsumons', []))
        for _ax_o in self.plan:
            if _ax_o.get('種別') != 'vary':
                continue
            _s_no = int(_ax_o['SitsumonNo'])
            if _s_no in _visit_order or _s_no in _base_def_o:
                continue
            if not _ref_o(_ax_o.get('軸名') or ''):
                continue
            _chain = self._reach_chain(_s_no, dict(forced_rows), _vary_sits_for_order)
            if not _chain:
                continue
            _sels_o = dict(forced_rows); _sels_o.update(_chain)
            _seq3 = FlowWalker(self.new_json,
                               vary_selections=_sels_o).walk().get('visited_sitsumons', [])
            _here = [x for x in _seq3
                     if x in _vary_sits_for_order and x not in _visit_order]
            for _s2 in _here:
                _pos = _seq3.index(_s2)
                for _prev in reversed(_seq3[:_pos]):
                    if _prev in _visit_order:
                        _visit_order[_s2] = _visit_order[_prev] + 0.01
                        break

        def _axis_order_key(p):
            return _visit_order.get(int(p['SitsumonNo']), 10**9 + int(p['SitsumonNo']))
        axes_sorted = sorted(self.plan, key=_axis_order_key)

        # vary 軸列挙 + G フィルタ (追加 Row より上の既存除外)
        vary_row_lists = []
        vary_added_rows = {}
        for ax in vary_axes:
            sit_no = int(ax['SitsumonNo'])
            rows = self._get_axis_rows(sit_no)
            reason = ax.get('変更理由', '')
            is_business_rule = '業務ルール' in reason
            added_idx = self._added_row_index_by_sit.get(sit_no)
            if is_business_rule:
                filtered = rows
                added_set = set()
            elif '新規到達' in reason:
                # 選択肢追加経由で新規到達した質問: 旧版では到達不能だったため
                #   実質「新規追加質問」 → 全選択肢を網羅 (#21 内径又は内空幅(各種))
                filtered = rows
                added_set = set()
            elif ('選択肢テキスト変更' in reason
                  and '選択肢追加' not in reason and '選択肢削除' not in reason
                  and added_idx is None and self.old_json is not None):
                # ※削除/追加と混在する軸はこの分岐に入れない (従来の flow_equiv
                #   代表行を維持。例: 06 作業内容 / 11 工種区分 = 分岐網羅を優先)
                # 新提案A: 文字修正のみの軸は「既定行 + 最長テキスト行」の2件。
                #   最長行 = 規格名計上のNGモード(文字の欠落)検証用。
                #   既定行を残すのは既存の既定経路TC(承認済みベースライン)を
                #   維持するため (既定を外すと下流の分岐/auto質問が変わってしまう)。
                # 最長行の候補は「テキストを含む表示」の行のみ
                #   (数値・記号値のみの行(例 -9999999999)は文字検証に不適。#19)
                def _is_texty(r):
                    s = str(r.get('display') or '').strip()
                    return bool(s) and not re.fullmatch(r'[-+0-9.,eE%~\s　()]*', s)
                _texty_rows = [r for r in rows if _is_texty(r)]
                longest = max(
                    _texty_rows, key=lambda r: len(str(r.get('display') or ''))
                ) if _texty_rows else None
                default = self._get_default_row(sit_no, rows)
                filtered = []
                _seen_ids = set()
                for r in (default, longest):
                    if r and r['row_id'] not in _seen_ids:
                        _seen_ids.add(r['row_id'])
                        filtered.append(r)
                added_set = set()
            elif self.old_json is not None and added_idx is None:
                # 差分型で新規追加行なし = 選択肢削除/値変更のみ。削除選択肢は新JSONに
                #   無く列挙不可。残行は「到達フロー(visited)が同じ＝値だけ違う」ものを
                #   代表1件に集約し、フローが分岐する選択肢は各代表を残す。
                #   (鉄筋径=集約で直積爆発回避 / 作業内容の撤去等=分岐保持)
                filtered = self._flow_equiv_rows(sit_no, rows)
                added_set = set()
            elif added_idx is None:
                # 新規工種モード (旧JSONなし)。選択肢でフローが分岐する軸のみ各代表を
                #   残し、フロー不変(値だけ。例 円周率/排ガス機械の選択)は代表1件に集約。
                #   → 分岐が必要な選択肢だけ vary 展開し直積爆発を防ぐ。
                filtered = self._flow_equiv_rows(sit_no, rows)
                added_set = set()
            else:
                old_s = self.old_json.sitsumon019_by_no.get(sit_no) if self.old_json else None
                old_set = set(
                    r['RowID'] for r in (old_s.get('SitRows', []) if old_s else [])
                    if r.get('Visible', True) and not r.get('IsFixed', False)
                )
                # 選択肢追加軸でも「フロー不変(値だけ違う＝共通サブルーチン的)」な
                #   追加選択肢は代表1件に集約する (#16/フロー分岐基準と同一思想。
                #   排ガス機械の選択など単価のみ変化しフローを分岐しない軸は全網羅が過剰)。
                #   フローを分岐する追加選択肢は _flow_equiv_rows が各代表を残す。
                #   集約後に追加行が1件も残らない場合 (代表が既存行) は、追加検証の
                #   ため追加行の先頭を1件残す (差分TC・選択肢適切さ観点を維持)。
                added_rows = rows[added_idx:]
                filtered = self._flow_equiv_rows(sit_no, added_rows)
                if not any(r['row_id'] not in old_set for r in filtered) and added_rows:
                    filtered = filtered + [added_rows[0]]
                added_set = set(r['row_id'] for r in filtered if r['row_id'] not in old_set)
            vary_row_lists.append((ax, filtered))
            vary_added_rows[ax['軸ID']] = added_set

        # fix/auto
        fix_chosen = {}
        for ax in fix_or_auto_axes:
            sit_no = int(ax['SitsumonNo'])
            rows = self._get_axis_rows(sit_no)
            if sit_no in forced_rows:
                forced_row = self._get_row_by_id(rows, forced_rows[sit_no])
                fix_chosen[ax['軸ID']] = forced_row or self._get_default_row(sit_no, rows)
            else:
                fix_chosen[ax['軸ID']] = self._get_default_row(sit_no, rows)

        # baseline scope
        baseline_walker = FlowWalker(self.new_json)
        baseline_result = baseline_walker.walk()
        baseline_scope = dict(baseline_walker.hyo._user_inputs)

        # combos 構築 + TC walker (2パス):
        #   1パス目で「どの TC でも訪問されない vary 軸」を検出したら、その軸を
        #   組合せ・列から除去して再構築する (到達しない軸での TC 増殖と
        #   無意味な確認観点を防ぐ。例: 06 の 作業内容(別分岐) / 労務費の適用)。
        for _pass in range(2):
            _reach_default_rows, _reach_specs = self._compute_reach_combos(
                vary_row_lists, forced_rows)
            # cartesian
            if vary_row_lists:
                combos = list(itertools.product(*[rs for _, rs in vary_row_lists]))
            else:
                combos = [tuple()]

            # 選択肢テキスト変更軸が複数あるとき、組合せは「全て既定行」or
            #   「全て最長行」の2通りだけ残す (文字検証は1TCで足りる。
            #   軸数分の直積爆発を防ぐ。例: 19 バックホウ 2^4=16 → 2)。
            _tx_axes = []
            for _i, (_ax, _rs) in enumerate(vary_row_lists):
                _rsn = _ax.get('変更理由', '')
                if ('選択肢テキスト変更' in _rsn and '選択肢追加' not in _rsn
                        and '選択肢削除' not in _rsn and len(_rs) > 1):
                    _tx_axes.append((_i, _rs[0]['row_id']))
            if len(_tx_axes) > 1:
                combos = [cb for cb in combos
                          if len({cb[_i]['row_id'] == _d
                                  for _i, _d in _tx_axes}) == 1]

            # multi-step到達用 combo を明示追加 (直積/剪定に依存せず必ず到達枝を1本含める)。
            #   到達comboは変更(方針言及軸)を exercise する差分TC。num_diff_tcs より前に
            #   入れて、テスト区分=差分/回帰 を内容で判定させる(状態戻し回帰の後ろ扱いにしない)。
            for _ov in _reach_specs:
                _rc = list(_reach_default_rows)
                for _gi, _r in _ov.items():
                    if 0 <= _gi < len(_rc):
                        _rc[_gi] = _r
                if len(_rc) == len(vary_row_lists):
                    combos.append(tuple(_rc))

            # G: 業務ルール vary 軸の「状態戻し回帰 TC」 1件追加
            num_diff_tcs = len(combos)
            biz_rule_axis_idx = None
            for i, (ax, _) in enumerate(vary_row_lists):
                if '業務ルール' in ax.get('変更理由', ''):
                    biz_rule_axis_idx = i
                    break
            if biz_rule_axis_idx is not None and combos and len(combos) > 1:
                regression_combo = list(combos[-1])
                biz_ax, biz_rows = vary_row_lists[biz_rule_axis_idx]
                if biz_rows:
                    regression_combo[biz_rule_axis_idx] = biz_rows[0]
                combos.append(tuple(regression_combo))


            # 全 TC walker
            tc_walks = []
            all_visited = set()
            for combo in combos:
                tc_vary_sels = dict(forced_rows)
                for (ax, _), chosen_row in zip(vary_row_lists, combo):
                    tc_vary_sels[int(ax['SitsumonNo'])] = chosen_row['row_id']
                tcw = FlowWalker(self.new_json, vary_selections=tc_vary_sels)
                tc_res = tcw.walk()
                visited = set(tc_res.get('visited_sitsumons', []))
                tc_walks.append({
                    'visited': visited,
                    'daika_flags': tc_res.get('daika_row_flags', {}),
                    'tc_scope': dict(tcw.hyo._user_inputs),
                    'closed': set(getattr(tcw, 'closed_sitsumons', ())),
                    # 07-①: auto軸の表示行を walker の実選択行に合わせるため保持
                    'sit_selections': dict(tc_res.get('sit_selections', {})),
                    'row_sources': dict(getattr(tcw, 'row_sources', {})),
                })
                all_visited.update(visited)

            # G2: 状態戻し回帰TC の基底 combo 補正
            #   業務ルール軸がその TC で閉じている/到達しない場合、切替操作が
            #   できないため、軸が開いている先頭 combo を基底にして再ウォーク。
            if biz_rule_axis_idx is not None and len(combos) > num_diff_tcs:
                biz_ax, biz_rows = vary_row_lists[biz_rule_axis_idx]
                biz_sit = int(biz_ax['SitsumonNo'])
                w = tc_walks[-1]
                if biz_sit not in w['visited'] or biz_sit in w['closed']:
                    for ci in range(num_diff_tcs):
                        wi = tc_walks[ci]
                        if biz_sit in wi['visited'] and biz_sit not in wi['closed']:
                            rc = list(combos[ci])
                            if biz_rows:
                                rc[biz_rule_axis_idx] = biz_rows[0]
                            combos[-1] = tuple(rc)
                            tc_vary_sels = dict(forced_rows)
                            for (ax, _), chosen_row in zip(vary_row_lists, combos[-1]):
                                tc_vary_sels[int(ax['SitsumonNo'])] = chosen_row['row_id']
                            tcw = FlowWalker(self.new_json, vary_selections=tc_vary_sels)
                            tc_res = tcw.walk()
                            tc_walks[-1] = {
                                'visited': set(tc_res.get('visited_sitsumons', [])),
                                'daika_flags': tc_res.get('daika_row_flags', {}),
                                'tc_scope': dict(tcw.hyo._user_inputs),
                                'closed': set(getattr(tcw, 'closed_sitsumons', ())),
                                'sit_selections': dict(tc_res.get('sit_selections', {})),
                                'row_sources': dict(getattr(tcw, 'row_sources', {})),
                            }
                            all_visited.update(tc_walks[-1]['visited'])
                            break

            unreachable = [i for i, (ax, _) in enumerate(vary_row_lists)
                           if int(ax['SitsumonNo']) not in all_visited]
            if _pass == 0 and unreachable:
                drop = set(unreachable)
                dropped_names = [vary_row_lists[i][0]['軸名'] for i in drop]
                print(f'  [vary除去] どのTCでも到達しない: {len(drop)}件 -> ' + ', '.join(dropped_names))
                dropped_ids = {vary_row_lists[i][0]['軸ID'] for i in drop}
                vary_row_lists = [x for i, x in enumerate(vary_row_lists) if i not in drop]
                vary_added_rows = {k: v for k, v in vary_added_rows.items() if k not in dropped_ids}
                continue
            break

        # A: 列除外 (vary は到達確認後の vary_row_lists 基準)
        vary_sit_nos = {int(ax['SitsumonNo']) for ax, _ in vary_row_lists}
        axes_displayed = [
            ax for ax in axes_sorted
            if int(ax['SitsumonNo']) in all_visited or int(ax['SitsumonNo']) in vary_sit_nos
        ]
        axes_excluded = [ax for ax in axes_sorted if ax not in axes_displayed]
        if axes_excluded:
            print(f'  [列除外] 到達せず: {len(axes_excluded)}件 -> '
                  + ', '.join(ax['軸名'] for ax in axes_excluded))

        # 列表示ポリシー: auto軸・定数設定fix軸は列から除外 (確認観点には axes_displayed を使用)
        axes_columns = [ax for ax in axes_displayed if not self._is_noise_column(ax)]
        col_excluded = [ax for ax in axes_displayed if ax not in axes_columns]
        if col_excluded:
            print(f'  [列除外] 自動確定/定数: {len(col_excluded)}件 -> '
                  + ', '.join(ax['軸名'] for ax in col_excluded))

        headers, s_present = self._build_headers(axes_columns)
        # Fix1: 同名の並行分岐質問(別SitsumonNo・同一表示名)を1列に統合する。
        #   旧→新で質問番号が振り直され、混合深さ等で機械計上の質問セットが
        #   浅用/深用に枝分かれするケース(例: (タイヤローラ)排ガス機械の選択 が
        #   No30 浅用 / No35 深用)。step2 が一方しか軸化できないため、列のSitが
        #   未到達でも同名の別Sitが到達していればその代表行を表示する。
        col_sibling_sits = {}
        for ax in axes_columns:
            _sno = int(ax['SitsumonNo'])
            _self_sit = self.new_json.sitsumon_by_no.get(_sno) or {}
            _label = _self_sit.get('Mesho')
            if not _label:
                continue
            _sibs = []
            for _it in self.new_json.data.get('SitsumonItem', []):
                _n = _it.get('SitsumonNo')
                if _n is None or _n == _sno:
                    continue
                if _it.get('SitsumonKind') == 19 and _it.get('Mesho') == _label:
                    _sibs.append(_n)
            if _sibs:
                col_sibling_sits[ax['軸ID']] = _sibs
        out_rows = []
        seen_tc_keys = set()
        s_to_row = {f'S{i}': i for i in range(1, 40)}

        has_any_input_axis = any(
            'SitsumonKind=17' in ax.get('備考', '')
            for ax in axes_displayed
        )

        for tc_idx, (combo, tcw_data) in enumerate(zip(combos, tc_walks), 1):
            tc_id = f'TC-{tc_idx:03d}'

            chosen_rows_by_ax = {}
            for ax_id, row in fix_chosen.items():
                if row is None:
                    continue
                chosen_rows_by_ax[ax_id] = row
            # 07-①: auto軸は walker が AutoSelectJoken で実選択した行を表示する
            #   (デフォルト行表示だと実機の自動確定実行と食い違う。
            #    例: 07 代価表の当り数量 O~AT2=ATJ=100 → 「100当り代価表」行)
            #   walker の選択経緯が 'auto' の場合のみ上書き (default/first は従来通り)
            for ax in fix_or_auto_axes:
                if ax['種別'] != 'auto':
                    continue
                sit_no = int(ax['SitsumonNo'])
                if tcw_data.get('row_sources', {}).get(sit_no) != 'auto':
                    continue
                sel = tcw_data.get('sit_selections', {}).get(sit_no)
                if sel is None:
                    continue
                w_row = self._get_row_by_id(self._get_axis_rows(sit_no), sel)
                if w_row is not None:
                    chosen_rows_by_ax[ax['軸ID']] = w_row
            for (ax, _), chosen_row in zip(vary_row_lists, combo):
                chosen_rows_by_ax[ax['軸ID']] = chosen_row

            hyo = KeisanHyo(self.new_json.data.get('KeisanItem', []))
            for vname, val in tcw_data['tc_scope'].items():
                try:
                    hyo.set_input(vname, val)
                except Exception:
                    pass

            daika_flags = tcw_data['daika_flags']

            # G: 差分/回帰判定
            #   新規工種モード (old_json 無し=差分基準なし) は「正常系」 固定。
            if self.old_json is None:
                test_kind = '新規歩掛'
            elif tc_idx > num_diff_tcs:
                # 業務ルール vary 軸 状態戻し回帰TC
                test_kind = '回帰'
            else:
                test_kind = '回帰'
                for (ax, _), chosen_row in zip(vary_row_lists, combo):
                    ax_id = ax['軸ID']
                    reason = ax.get('変更理由', '')
                    if chosen_row['row_id'] in vary_added_rows.get(ax_id, set()):
                        test_kind = '差分'
                        break
                    if '業務ルール' in reason or '新規追加質問' in reason:
                        test_kind = '差分'
                        break

            row_data = [tc_id, test_kind]
            # J1: TC ごとに、その vary 軸が訪問されない場合は "-" 表記
            tc_visited = tcw_data['visited']
            _closed_set = tcw_data.get('closed', ())
            for ax in axes_columns:
                sit_no = int(ax['SitsumonNo'])
                row = chosen_rows_by_ax.get(ax['軸ID'])
                if sit_no in tc_visited and sit_no not in _closed_set:
                    row_data.append(self._display_for_tab(row, hyo) if row else '')
                    continue
                # Fix1: 自Sitが未到達でも同名の並行分岐質問(別Sit)が到達していれば
                #   その代表行(既定行)を表示する。
                _shown = None
                for _sib in col_sibling_sits.get(ax['軸ID'], []):
                    if _sib in tc_visited and _sib not in _closed_set:
                        _sib_rows = self._get_axis_rows(_sib)
                        _sib_row = self._get_default_row(_sib, _sib_rows)
                        _shown = (self._display_for_tab(_sib_row, hyo) if _sib_row else '')
                        break
                # この TC では訪問されない/レベル変数で閉じて自動確定 (= UI 非表示)
                row_data.append(_shown if _shown is not None else '-')

            for v in s_present:
                row_no = s_to_row.get(v)
                if row_no is not None:
                    flag = daika_flags.get((1, row_no))
                    if flag is not None and flag == 0:
                        row_data.append('')
                        continue
                try:
                    val = hyo.value(v)
                    fmt = self._fmt_decimal(val)
                    if has_any_input_axis and fmt == '0':
                        row_data.append('計算結果が正しいか')
                    else:
                        row_data.append(fmt)
                except ExternalReferenceError:
                    row_data.append('(外部単価依存)')
                except ExpressionError:
                    row_data.append('(評価不能)')
                except Exception as e:
                    row_data.append(f'(エラー: {e.__class__.__name__})')

            checks = []
            kikaku_checks = []  # 規格名計上は別列に分離
            for (ax, _), chosen_row in zip(vary_row_lists, combo):
                # J4: この TC で到達しない vary 軸の確認観点は出さない
                if int(ax['SitsumonNo']) not in tc_visited:
                    continue
                reason = ax.get('変更理由', '')
                # 規格名計上の発火対象: 新規質問・選択肢追加に加え、選択肢削除・
                #   選択肢テキスト変更も含める
                #   (選択肢が削除されると計上される規格名の集合が変わるため要確認。
                #    例: 11 工種区分=選択肢削除で規格名計上が変化。
                #    選択肢の文字修正は計上文字列に直結。例: 12 機械区分=文字修正)。
                is_diff_vary = (('新規追加質問' in reason) or ('選択肢追加' in reason)
                                or ('選択肢削除' in reason)
                                or ('選択肢テキスト変更' in reason)
                                or ('新規到達' in reason))
                # 数値入力軸(SitsumonKind=17)は display='任意' のプレースホルダで、
                # 「任意」は積算シミュレート時に表示されない固定文言にすぎない。
                # この場合は「表示される質問が外部設計と正しいか」に統一する。
                is_numeric_input = (chosen_row.get('display') == '任意' and chosen_row.get('row_id') == 0)
                # 表示ラベルは列と同じ active-tab 解決を使う (複数タブ質問で、列は
                #   基本表なのに確認テキストだけ別タブのラベルになる不整合を防ぐ。
                #   例: 10 土留方式の種類 → 基本表「無し/自立式」が正)。
                disp_label = self._display_for_tab(chosen_row, hyo) or chosen_row.get('display', '')
                if is_numeric_input:
                    check_text = f'・{ax["軸名"]}（表示される質問）が外部設計と正しいか'
                else:
                    check_text = f'・「{disp_label}」と表示されているが、外部設計と正しいか'
                if self.old_json is None:
                    # 新規工種モード: 差分が無いため全選択肢を網羅。
                    #   各選択肢が外部設計通りか (表示・計上) を確認観点に。
                    checks.append(f'{ax["軸名"]}(全選択肢網羅)')
                    checks.append(check_text)
                elif '新規追加質問' in reason:
                    checks.append(f'{ax["軸名"]}(2026年新規追加)')
                    checks.append(check_text)
                elif '選択肢追加' in reason:
                    checks.append(f'{ax["軸名"]}(選択肢追加)')
                    checks.append(check_text)
                elif '選択肢削除' in reason:
                    checks.append(f'{ax["軸名"]}(選択肢削除)')
                    checks.append(check_text)
                    deleted = self._deleted_options(int(ax['SitsumonNo']))
                    if deleted:
                        checks.append('・削除された選択肢(' + '、'.join(deleted) + ')が表示されないこと')
                elif '選択肢テキスト変更' in reason:
                    # 文字修正のみの軸: 最長選択肢で表示文字列を検証 (新提案A)
                    checks.append(f'{ax["軸名"]}(選択肢文字修正)')
                    checks.append(check_text)
                elif '新規到達' in reason:
                    checks.append(f'{ax["軸名"]}(追加選択肢経由の新規到達・全選択肢網羅)')
                    checks.append(check_text)
                elif reason.startswith('修正方針:'):
                    # 修正方針の文面から確認観点を生成 (フロー見直し起点の軸)
                    _ck = self._intent_check_text(ax['軸名'])
                    checks.append(_ck if _ck else check_text)
                # J2: 規格名計上の確認観点
                #   - 修正対象 (差分検出 vary 軸) のみ
                #   - JSON 上で KikakuKeijoNaiyo が設定されている軸のみ
                if (is_diff_vary or self.old_json is None) and self._has_kikaku_keijo(int(ax['SitsumonNo'])):
                    if ('選択肢テキスト変更' in reason
                            and '選択肢追加' not in reason
                            and '選択肢削除' not in reason):
                        # 新提案A: NG=文字の欠落 → 最長選択肢で計上文字列の欠落を検証
                        kikaku_checks.append(
                            f'・{ax["軸名"]} の規格名計上が意図通りの場所に正しく計上されているか'
                            '(最長選択肢で検証・文字の欠落がないか)')
                    else:
                        kikaku_checks.append(f'・{ax["軸名"]} の規格名計上が意図通りの場所に正しく計上されているか')
            # J2拡張: 軸に上がらない質問(自動確定エコー等)でも、選択肢テキスト
            #   変更かつ規格名計上を持つ質問がこの TC で visited なら観点を出す
            #   (例: 12 機械区分71 = 機械質量区分のエコー。軸=機械質量区分側を
            #    変えると計上文字列が変わるため、文字の欠落検証が必要)。
            _vary_sit_nos = {int(a['SitsumonNo']) for a, _ in vary_row_lists}
            for _sn in sorted(self._text_changed_sits()):
                if _sn in _vary_sit_nos or _sn not in tc_visited:
                    continue
                if not self._has_kikaku_keijo(_sn):
                    continue
                _nm = self.new_json.get_sitsumon_name(_sn)
                kikaku_checks.append(
                    f'・{_nm}(選択肢文字修正・自動確定) の規格名計上が意図通りの'
                    '場所に正しく計上されているか(文字の欠落がないか)')
            # G: 状態戻し回帰TC の確認観点
            if tc_idx > num_diff_tcs and biz_rule_axis_idx is not None:
                biz_ax = vary_row_lists[biz_rule_axis_idx][0]
                checks.append(f'・{biz_ax["軸名"]} を切り替えた後、最初の選択肢に戻したとき、計算結果が初期状態と一致すること(状態戻し回帰)')
            # 初期値変更の確認観点 (fix/auto軸でも、デフォルト選択が変わった質問は観点を出す)
            for ax in axes_displayed:
                if '初期値変更' not in (ax.get('変更理由') or ''):
                    continue
                row = chosen_rows_by_ax.get(ax['軸ID'])
                disp = self._display_for_tab(row, hyo) if row else ''
                checks.append(f'{ax["軸名"]}(初期値変更)')
                checks.append(f'・「{disp}」と表示されているが、外部設計と正しいか(初期値の変更)')

            # 代価表行追加の確認観点 (質問の変更がなく代価表行が追加されたケース)
            #   例: 01 回航費 (S1-S5 が無く代価表数率が出力)。期待値の代わりに
            #   「追加された行と数量が外部設計に沿っているか」 を確認観点として出す。
            if self._has_added_daika():
                checks.append('・追加された代価表行と数量(数率)が外部設計に沿っているか')
            # 文字のみの修正(質問表示名/計算表名称)の確認観点 (#13/#14)
            for _nc_sn, _nc_text in self._name_change_checks():
                if _nc_sn is not None and _nc_sn not in tc_visited:
                    continue
                checks.append(_nc_text)
            # 新提案B: 子代価の送り変数増減・計上先変更の確認観点 (#15)
            for _cd_sn, _cd_text in self._child_daika_checks():
                if _cd_sn not in tc_visited:
                    continue
                checks.append(_cd_text)
            # FB②: 確認観点が無い(条件変更なし)TCは「選択肢が商品と変わっていないこと」を出す
            row_data.append('\n'.join(checks) if checks
                            else '・選択肢が商品(現行版)と変わっていないこと')
            # 規格名計上に影響する検知が無い TC は「-」(確認不要の明示。#13/#14 FB)
            row_data.append('\n'.join(kikaku_checks) or '-')
            # J4: 重複TC除去 (vary 軸がこの TC で到達しない場合、選択違いでも
            #     条件・期待値・確認観点が同一になる。同一行は1件に畳む)
            dup_key = tuple(row_data[1:])
            if dup_key in seen_tc_keys:
                continue
            seen_tc_keys.add(dup_key)
            out_rows.append(row_data)

        # テストID再採番 (重複除去で欠番が出るため)
        for i, r in enumerate(out_rows, 1):
            r[0] = f'TC-{i:03d}'

        return [headers] + out_rows

    @staticmethod
    def _fmt_decimal(v):
        s = str(v)
        if '.' in s:
            s = s.rstrip('0').rstrip('.')
            if not s:
                s = '0'
        return s


def run(plan_csv_path, new_json_path, output_path, old_json_path=None,
        ref_json_path=None):
    gen = ColumnTCGenerator(plan_csv_path, new_json_path, old_json_path,
                            ref_json_path=ref_json_path)
    rows = gen.generate()
    BugakariJSON.write_csv(rows, output_path)
    n = len(rows) - 1
    cols = len(rows[0]) if rows else 0
    print(f'テストケースCSV生成完了: {output_path}')
    print(f'  TC件数: {n} / 列数: {cols}')


if __name__ == '__main__':
    _args = sys.argv[1:]
    _ref = None
    if '--ref' in _args:
        _i = _args.index('--ref')
        _ref = _args[_i + 1]
        del _args[_i:_i + 2]
    if len(_args) < 3:
        print('Usage: python generate_csv.py <plan_csv> <new_json> <output_csv>'
              ' [old_json] [--ref 参考json]')
        sys.exit(1)
    _old = _args[3] if len(_args) > 3 else None
    run(_args[0], _args[1], _args[2], _old, _ref)
