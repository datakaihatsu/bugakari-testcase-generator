# -*- coding: utf-8 -*-
"""配布zipの差分リビルド（バージョン履歴.md リリース手順 3）。

  既存zip（python-embed / libs / bat / txt を含む完全自己完結版）を展開し、
  app/ だけをリポジトリの最新で置き換えて再zipする。
  bat はバイト同一に保つ（2026-07-10 の起動不具合の教訓: bat は触らない）。

使い方:
  python scripts/build_dist_zip.py <既存zip> <出力zip> [--with-guide]
    --with-guide … 資料/ の制限事項ガイド(xlsx・現在の版)を zip ルートに同梱する
"""
import io
import os
import sys
import glob
import shutil
import zipfile
import tempfile

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # 02_第2弾_G条件
sys.path.insert(0, os.path.join(BASE, 'webapp'))
from version import APP_VERSION, VERSION_LABEL   # noqa: E402

# app/ に入れるもの（配布物 v1.2.3 の構成 ＋ v1.3.0 で増えた gjoken_reach.py
#   ＋ v1.4.0 で増えた段表記の gjoken_dan.py / gjoken_dan_read.py）
APP_FILES = ['gen_gjoken.py', 'gen_tc_from_gjoken.py', 'gjoken_reach.py',
             'gjoken_dan.py', 'gjoken_dan_read.py']
APP_DIRS = {
    'engine': ('*.py',),
    'step1_diff': ('*.py',),
    'step2_proposals': ('*.py',),
    'step3_csv': ('*.py',),
    'webapp': ('*.py', 'config.json'),
    'webapp/static': ('*',),
}
EXCLUDE_PREFIX = ('test_',)          # テストは同梱しない


def collect_app():
    out = []
    for f in APP_FILES:
        out.append((os.path.join(BASE, f), 'app/' + f))
    for d, pats in APP_DIRS.items():
        for pat in pats:
            for p in sorted(glob.glob(os.path.join(BASE, d, pat))):
                if not os.path.isfile(p):
                    continue
                name = os.path.basename(p)
                if name.startswith(EXCLUDE_PREFIX) or name.endswith('.pyc'):
                    continue
                out.append((p, 'app/%s/%s' % (d, name)))
    return out


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    with_guide = '--with-guide' in sys.argv
    if len(args) < 2:
        print(__doc__)
        return 1
    src_zip, dst_zip = args
    work = tempfile.mkdtemp(prefix='dist_')
    with zipfile.ZipFile(src_zip) as z:
        z.extractall(work)
    # app/ を丸ごと入れ替える
    shutil.rmtree(os.path.join(work, 'app'), ignore_errors=True)
    files = collect_app()
    for src, rel in files:
        dst = os.path.join(work, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
    # 制限事項ガイド
    if with_guide:
        g = glob.glob(os.path.join(BASE, '資料', '*_v%s.xlsx' % APP_VERSION))
        if not g:
            print('ガイド xlsx (v%s) が 資料/ にありません。先に scripts/gen_limitations_xlsx.py を実行してください。'
                  % APP_VERSION)
            return 1
        # 既存zipに入っていた古い版のガイドは外す（v1.5.0 で v1.4.0 版が残っていた）
        for old in glob.glob(os.path.join(work, '*（制限事項と切り分けガイド）_v*.xlsx')):
            os.remove(old)
        shutil.copy2(g[0], os.path.join(work, os.path.basename(g[0])))
    # 再zip（相対パスは / 区切り・ディレクトリエントリなし）
    if os.path.exists(dst_zip):
        os.remove(dst_zip)
    n = 0
    with zipfile.ZipFile(dst_zip, 'w', zipfile.ZIP_DEFLATED) as z:
        for root, _dirs, fnames in os.walk(work):
            for fn in sorted(fnames):
                p = os.path.join(root, fn)
                rel = os.path.relpath(p, work).replace(os.sep, '/')
                z.write(p, rel)
                n += 1
    shutil.rmtree(work, ignore_errors=True)
    print('作成: %s' % dst_zip)
    print('  版: %s / エントリ %d / app ファイル %d%s'
          % (VERSION_LABEL, n, len(files), ' / ガイド同梱' if with_guide else ''))
    return 0


if __name__ == '__main__':
    sys.exit(main())
