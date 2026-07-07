# -*- coding: utf-8 -*-
"""
locate.py ― 歩掛JSON特定ロジック（ロジック層 / 純ライブラリ）

運用化①の前段。運用者が入力した「Gaia9キー」または「歩掛キー」から、
ローカル格納場所にある歩掛JSONを特定する。

移植元: 27.Gaiaと条件の乖離チェック【施行前】/プログラム/Gaia9乖離チェック.py
  - KeyMapping           : ExpCDConvert.json で (ShochoCD,UserMD,CD) → 歩掛キー
  - find_bugakari_json   : <key>.<年度>.<年月日>.json を今日以下で最大版=現行リリース版で選定
（27は読み取り専用参照。本ファイルは独立コピーで、27を import しない）

設計書: 運用化設計書_2026-07-07.md §2/§6/Q4/Q6。
方針: import で副作用を持たない純ライブラリ。パスは呼び出し側(config)から渡す。
"""

import os
import re
import glob
import json
import datetime

# 既定パス（27と同一。実運用では config.json / UI 設定欄で上書き可能）
DEFAULT_EXPCD = r'C:/ProgramData/CoBeing/GaiaCloud/DB/Common/ExpCDConvert.json'
DEFAULT_BUGAKARI = r'C:/ProgramData/CoBeing/GaiaCloud/DB/Bugakari'
DBH_KOSHU_GROUP = 65  # tdhKoshuGroup(工種分類)。4要素Gaia9キーの先頭はこれであること


# ----------------------------------------------------------------------------
# キー解析
# ----------------------------------------------------------------------------
def _split_nums(text):
    """'65-14-160-20350' → [65,14,160,20350]。数値以外を含めば ValueError。"""
    parts = [p for p in re.split(r'[-\s]+', text.strip()) if p != '']
    if not parts or not all(re.fullmatch(r'-?\d+', p) for p in parts):
        raise ValueError('数値以外を含むキー: %r' % text)
    return [int(p) for p in parts]


def parse_gaia9_key(text):
    """Gaia9キー文字列 → (ShochoCD, UserMD, CD)。
    '65-14-160-20350'(4要素・先頭65=工種分類) / '14-160-20350'(3要素) を許容。
    移植元 KeyMapping.parse_gaia9_key と同一挙動。"""
    nums = _split_nums(text)
    if len(nums) == 4:
        dbh, shocho, usermd, cd = nums
        if dbh != DBH_KOSHU_GROUP:
            raise ValueError('先頭(DBH)が工種分類(%d)ではありません: %r' % (DBH_KOSHU_GROUP, text))
        return shocho, usermd, cd
    if len(nums) == 3:
        return nums[0], nums[1], nums[2]
    raise ValueError('Gaia9キーは3要素または4要素で指定してください: %r' % text)


def classify_key(text):
    """入力文字列を判定する。
    戻り値: ('bugakari', '160-1351') = 歩掛キー直接指定（2要素・Q4で許可）
            ('gaia9', (shocho,usermd,cd)) = Gaia9キー（3/4要素）
    数値以外・要素数不正は ValueError。"""
    nums = _split_nums(text)
    if len(nums) == 2:
        return 'bugakari', '%d-%d' % (nums[0], nums[1])
    return 'gaia9', parse_gaia9_key(text)


# ----------------------------------------------------------------------------
# (1) Gaia9キー → 歩掛キー 変換
# ----------------------------------------------------------------------------
class KeyMapping:
    """ExpCDConvert.json を読み、(ShochoCD, UserMD, CD) → 歩掛キー一覧 を引く。
    移植元 KeyMapping と同一。"""

    def __init__(self, expcd_path=DEFAULT_EXPCD):
        with open(expcd_path, encoding='utf-8-sig') as f:
            data = json.load(f)
        self._map = {}  # (ShochoCD, UserMD, CD) -> list[(UserMD, CD)] (重複なし・出現順)
        for r in data.get('ExpCDConvertList', []):
            g = (r.get('Gaia9ShochoCD'), r.get('Gaia9UserMD'), r.get('Gaia9CD'))
            key = (r.get('UserMD'), r.get('CD'))
            lst = self._map.setdefault(g, [])
            if key not in lst:
                lst.append(key)

    def to_bugakari_keys(self, gaia9_tuple):
        """(ShochoCD,UserMD,CD) → 歩掛キー文字列の一覧(['160-1351', ...])。"""
        found = self._map.get(tuple(gaia9_tuple), [])
        return ['%d-%d' % (u, c) for (u, c) in found]


# ----------------------------------------------------------------------------
# (2) 歩掛JSON 検索・版選定
# ----------------------------------------------------------------------------
def _today_int():
    return int(datetime.date.today().strftime('%Y%m%d'))


def find_bugakari_versions(bugakari_root, bugakari_key, today_int=None):
    """歩掛キーで歩掛JSONを検索し、版候補一覧と選定版を返す。
    ファイル名: <歩掛キー>.<適用年度>.<適用年月日>.json（Memo等の派生は除外）。
    選定規則: 適用年月日 ≤ today_int の最大。すべて未来なら最も近い(=最小)版。

    戻り値: {
        'candidates': [{'key','nendo','ymd','path','is_chosen'} ...]（ymd昇順）,
        'chosen': {同上} | None,
    }
    移植元 find_bugakari_json を UI 提示用に構造化（選定規則は不変）。"""
    if today_int is None:
        today_int = _today_int()
    usermd = bugakari_key.split('-')[0]
    # UserMD サブフォルダ配下を優先探索、無ければ全体を再帰探索（27と同じ2段）
    patterns = [
        os.path.join(bugakari_root, usermd, '**', bugakari_key + '.*.json'),
        os.path.join(bugakari_root, '**', bugakari_key + '.*.json'),
    ]
    hits = []  # (ymd, nendo, path)
    seen = set()
    for pat in patterns:
        for p in glob.glob(pat, recursive=True):
            np = os.path.normcase(os.path.abspath(p))
            if np in seen:
                continue
            seen.add(np)
            name = os.path.basename(p)
            m = re.fullmatch(re.escape(bugakari_key) + r'\.(\d+)\.(\d+)\.json', name)
            if not m:
                continue
            hits.append((int(m.group(2)), int(m.group(1)), p))
        if hits:
            break
    if not hits:
        return {'candidates': [], 'chosen': None}

    past = [h for h in hits if h[0] <= today_int]
    chosen = max(past, key=lambda h: h[0]) if past else min(hits, key=lambda h: h[0])

    candidates = []
    for ymd, nendo, path in sorted(hits, key=lambda h: h[0]):
        candidates.append({
            'key': bugakari_key, 'nendo': nendo, 'ymd': ymd, 'path': path,
            'is_chosen': (ymd == chosen[0] and path == chosen[2]),
        })
    chosen_d = next(c for c in candidates if c['is_chosen'])
    return {'candidates': candidates, 'chosen': chosen_d}


# ----------------------------------------------------------------------------
# (3) 高水準API: 入力文字列 → 歩掛キー群 × 版候補
# ----------------------------------------------------------------------------
def resolve(input_text, expcd_path=DEFAULT_EXPCD, bugakari_root=DEFAULT_BUGAKARI,
            today_int=None):
    """運用者入力（Gaia9キー or 歩掛キー）から歩掛キー群と各版候補を解決する。
    ①タブのバックエンド。version選択はUIに委ね、既定=is_chosen を提示する。

    戻り値: {
      'kind': 'gaia9' | 'bugakari',
      'gaia9': (shocho,usermd,cd) | None,
      'bugakari_keys': ['160-1351', ...],   # Gaia9→複数ヒットあり得る
      'results': [{'bugakari_key', 'candidates':[...], 'chosen':{...}|None} ...],
      'error': None | str,
    }
    例外は投げず error 文字列に載せる（UIで表示するため）。"""
    out = {'kind': None, 'gaia9': None, 'bugakari_keys': [], 'results': [], 'error': None}
    try:
        kind, val = classify_key(input_text)
    except ValueError as e:
        out['error'] = str(e)
        return out

    out['kind'] = kind
    if kind == 'bugakari':
        out['bugakari_keys'] = [val]
    else:
        out['gaia9'] = val
        try:
            km = KeyMapping(expcd_path)
        except (OSError, ValueError) as e:
            out['error'] = 'ExpCDConvert.json 読込失敗: %s' % e
            return out
        keys = km.to_bugakari_keys(val)
        if not keys:
            out['error'] = 'Gaia9キー %s に対応する歩掛キーが ExpCDConvert.json にありません' % (val,)
            return out
        out['bugakari_keys'] = keys

    for bk in out['bugakari_keys']:
        v = find_bugakari_versions(bugakari_root, bk, today_int)
        out['results'].append({'bugakari_key': bk, **v})
    if all(not r['candidates'] for r in out['results']):
        out['error'] = '歩掛JSONが見つかりません（格納場所: %s）' % bugakari_root
    return out


if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print('Usage: python3 locate.py <Gaia9キー or 歩掛キー> [expcd] [bugakari_root]')
        sys.exit(1)
    kw = {}
    if len(sys.argv) > 2:
        kw['expcd_path'] = sys.argv[2]
    if len(sys.argv) > 3:
        kw['bugakari_root'] = sys.argv[3]
    r = resolve(sys.argv[1], **kw)
    print(json.dumps(r, ensure_ascii=False, indent=2))
