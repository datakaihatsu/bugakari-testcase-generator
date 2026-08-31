# -*- coding: utf-8 -*-
"""
version.py ― 配布ツールのバージョン定義（唯一の正）

受け渡し先と「どのビルドを使っているか」を突き合わせるための単一情報源。
画面ヘッダー右上に VERSION_LABEL を表示する（/api/config 経由）。

リリース時はここだけ書き換え、`../バージョン履歴.md` に1行追記する。
採番: セマンティック（MAJOR.MINOR.PATCH）＋ビルド日
  MAJOR … 使い方が変わる非互換変更
  MINOR … 機能追加（タブ追加・新モード等）
  PATCH … 不具合修正・生成ロジックの補正
"""

APP_VERSION = '1.1.2'
BUILD_DATE = '2026-08-31'          # YYYY-MM-DD（配布zipを作った日）
VERSION_LABEL = 'v%s (%s)' % (APP_VERSION, BUILD_DATE)
