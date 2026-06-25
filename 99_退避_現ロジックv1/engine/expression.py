"""
歩掛Expression評価器

参照: source_ref/sirius/Being/Core/Expressions/Parser.cs に準拠
仕様要点:
- 主型: Decimal (10進数で歩掛精度を確保)
- ^ (べき乗) のみ float で計算。特定パターン (10^X, X^2, (x*x)^0.5 等) は Decimal に戻す
- 0除算 → 0 (例外でない)
- 負底のべき乗 → 0
- 比較演算結果: 真=Decimal(1), 偽=Decimal(0)
- IF(c, t, f): c != 0 → t、else f
- && は IF(L, R, 0)、|| は IF(L, 1, R) として AST 展開
- 識別子は ASCII英字, ', @, _, ~, および Unicode 128+ で開始可
- @N は外部単価参照 → ExternalReferenceError
"""

import math
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP, ROUND_DOWN, ROUND_UP, getcontext
from typing import Any, Callable, List, Optional

getcontext().prec = 28


# =====================================================================
# 例外
# =====================================================================

class ExpressionError(Exception):
    """式の構文・評価エラー"""
    def __init__(self, message, at=-1, inner=None):
        super().__init__(message)
        self.at = at
        self.inner = inner


class ExternalReferenceError(ExpressionError):
    """外部単価DB参照 (@N) で評価不能"""
    pass


# =====================================================================
# トークン定数
# =====================================================================

class Tok:
    EOF = 'EOF'
    LP, RP = '(', ')'
    AST, PLUS, COMMA, HYPHEN, SLASH, HAT = '*', '+', ',', '-', '/', '^'
    LT, LE, GT, GE, EQ, NE = '<', '<=', '>', '>=', '==', '!='
    AND, OR = '&&', '||'
    NUM, IDENT, STR = 'NUM', 'IDENT', 'STR'


# =====================================================================
# 字句解析
# =====================================================================

def _is_ident_head(c: str) -> bool:
    if not c:
        return False
    if c.isalpha():
        return True
    if c in "'@_~":
        return True
    return ord(c) >= 128


def _is_ident_body(c: str) -> bool:
    if not c:
        return False
    if c.isalnum():
        return True
    if c in '"\'@_~':
        return True
    return ord(c) >= 128


class Lexer:
    def __init__(self, source: str):
        self.src = source
        self.pos = 0
        self.tok = None
        self.value = None
        self.at = 0
        self._next()

    def _peek(self) -> str:
        return self.src[self.pos] if self.pos < len(self.src) else ''

    def _advance(self):
        self.pos += 1

    def next(self):
        self._next()

    def _next(self):
        while True:
            while self._peek() and self._peek().isspace():
                self._advance()
            self.at = self.pos
            c = self._peek()
            if not c:
                self.tok = Tok.EOF
                return
            if c == '!':
                self._advance()
                if self._peek() != '=':
                    raise ExpressionError("無効な文字です。", self.pos)
                self._advance()
                self.tok = Tok.NE
                return
            if c == '"':
                self._advance()
                buf = []
                while True:
                    cc = self._peek()
                    if cc == '"':
                        self._advance()
                        if self._peek() != '"':
                            break
                    if not self._peek():
                        raise ExpressionError("'\"' が必要です。", self.pos)
                    buf.append(self._peek())
                    self._advance()
                self.value = ''.join(buf)
                self.tok = Tok.STR
                return
            if c == '&':
                self._advance()
                if self._peek() != '&':
                    raise ExpressionError("無効な文字です。", self.pos)
                self._advance()
                self.tok = Tok.AND
                return
            if c == '(':
                self._advance(); self.tok = Tok.LP; return
            if c == ')':
                self._advance(); self.tok = Tok.RP; return
            if c == '*':
                self._advance(); self.tok = Tok.AST; return
            if c == '+':
                self._advance(); self.tok = Tok.PLUS; return
            if c == ',':
                self._advance(); self.tok = Tok.COMMA; return
            if c == '-':
                self._advance(); self.tok = Tok.HYPHEN; return
            if c == '/':
                self._advance()
                if self._peek() == '/':
                    while self._peek() and self._peek() != '\n':
                        self._advance()
                    if self._peek():
                        self._advance()
                    continue
                self.tok = Tok.SLASH
                return
            if c == '<':
                self._advance()
                if self._peek() == '=':
                    self._advance(); self.tok = Tok.LE; return
                if self._peek() == '>':
                    self._advance(); self.tok = Tok.NE; return
                self.tok = Tok.LT
                return
            if c == '=':
                self._advance()
                if self._peek() != '=':
                    raise ExpressionError("無効な文字です。", self.pos)
                self._advance(); self.tok = Tok.EQ; return
            if c == '>':
                self._advance()
                if self._peek() == '=':
                    self._advance(); self.tok = Tok.GE; return
                self.tok = Tok.GT
                return
            if c == '^':
                self._advance(); self.tok = Tok.HAT; return
            if c == '|':
                self._advance()
                if self._peek() != '|':
                    raise ExpressionError("無効な文字です。", self.pos)
                self._advance(); self.tok = Tok.OR; return

            # 数値または識別子
            buf = []
            while self._peek() and self._peek().isdigit():
                buf.append(self._peek())
                self._advance()
            if self._peek() == '.':
                buf.append(self._peek())
                self._advance()
                while self._peek() and self._peek().isdigit():
                    buf.append(self._peek())
                    self._advance()
                self.value = Decimal(''.join(buf))
                self.tok = Tok.NUM
                return
            if _is_ident_head(self._peek()):
                while _is_ident_body(self._peek()):
                    buf.append(self._peek())
                    self._advance()
                self.value = ''.join(buf)
                self.tok = Tok.IDENT
                return
            if buf:
                self.value = Decimal(''.join(buf))
                self.tok = Tok.NUM
                return
            raise ExpressionError(f"無効な文字です: '{c}'", self.pos)


# =====================================================================
# AST
# =====================================================================

@dataclass
class Literal:
    value: Any


@dataclass
class GetVar:
    name: str
    at: int


@dataclass
class Call:
    name: str
    args: List[Any]
    at: int


@dataclass
class UnaryOp:
    op: str
    x: Any


@dataclass
class BinOp:
    op: str
    x: Any
    y: Any


@dataclass
class IfExpr:
    cond: Any
    x: Any
    y: Any


# =====================================================================
# 構文解析 (再帰下降)
# =====================================================================

class Parser:
    def __init__(self, lexer: Lexer):
        self.lex = lexer

    @staticmethod
    def parse(source: str):
        lex = Lexer(source)
        if lex.tok == Tok.EOF:
            return None
        p = Parser(lex)
        ast = p._expr()
        if lex.tok != Tok.EOF:
            raise ExpressionError("無効なトークンです。", lex.at)
        return ast

    def _primary(self):
        tok = self.lex.tok
        if tok == Tok.LP:
            self.lex.next()
            x = self._expr()
            if self.lex.tok != Tok.RP:
                raise ExpressionError("')' が必要です。", self.lex.at)
            self.lex.next()
            return x
        if tok == Tok.NUM:
            v = Literal(self.lex.value)
            self.lex.next()
            return v
        if tok == Tok.IDENT:
            at = self.lex.at
            name = self.lex.value
            self.lex.next()
            if name.lower() == 'if':
                if self.lex.tok != Tok.LP:
                    raise ExpressionError("'(' が必要です。", self.lex.at)
                self.lex.next()
                cond = self._expr()
                if self.lex.tok != Tok.COMMA:
                    raise ExpressionError("',' が必要です。", self.lex.at)
                self.lex.next()
                x = self._expr()
                if self.lex.tok != Tok.COMMA:
                    raise ExpressionError("',' が必要です。", self.lex.at)
                self.lex.next()
                y = self._expr()
                if self.lex.tok != Tok.RP:
                    raise ExpressionError("')' が必要です。", self.lex.at)
                self.lex.next()
                return IfExpr(cond, x, y)
            if self.lex.tok != Tok.LP:
                return GetVar(name, at)
            self.lex.next()
            args = []
            if self.lex.tok != Tok.RP:
                while True:
                    args.append(self._expr())
                    if self.lex.tok != Tok.COMMA:
                        break
                    self.lex.next()
                if self.lex.tok != Tok.RP:
                    raise ExpressionError("')' が必要です。", self.lex.at)
            self.lex.next()
            return Call(name, args, at)
        if tok == Tok.STR:
            v = Literal(self.lex.value)
            self.lex.next()
            return v
        raise ExpressionError(f"無効なトークンです (tok={tok})。", self.lex.at)

    def _unary(self):
        if self.lex.tok == Tok.PLUS:
            self.lex.next()
            return self._unary()
        if self.lex.tok == Tok.HYPHEN:
            self.lex.next()
            return UnaryOp('-', self._unary())
        return self._primary()

    def _exponential(self):
        # ^ は右結合
        x = self._unary()
        if self.lex.tok != Tok.HAT:
            return x
        self.lex.next()
        y = self._exponential()
        return BinOp('^', x, y)

    def _multiplicative(self):
        x = self._exponential()
        while self.lex.tok in (Tok.AST, Tok.SLASH):
            op = '*' if self.lex.tok == Tok.AST else '/'
            self.lex.next()
            x = BinOp(op, x, self._exponential())
        return x

    def _additive(self):
        x = self._multiplicative()
        while self.lex.tok in (Tok.PLUS, Tok.HYPHEN):
            op = '+' if self.lex.tok == Tok.PLUS else '-'
            self.lex.next()
            x = BinOp(op, x, self._multiplicative())
        return x

    def _relational(self):
        x = self._additive()
        op_map = {Tok.LT: '<', Tok.LE: '<=', Tok.GT: '>', Tok.GE: '>=', Tok.EQ: '==', Tok.NE: '!='}
        while self.lex.tok in op_map:
            op = op_map[self.lex.tok]
            self.lex.next()
            x = BinOp(op, x, self._additive())
        return x

    def _and_also(self):
        x = self._relational()
        while self.lex.tok == Tok.AND:
            self.lex.next()
            # && は IF(L, R, 0) として展開
            x = IfExpr(x, self._relational(), Literal(Decimal(0)))
        return x

    def _expr(self):
        x = self._and_also()
        while self.lex.tok == Tok.OR:
            self.lex.next()
            # || は IF(L, 1, R) として展開
            x = IfExpr(x, Literal(Decimal(1)), self._and_also())
        return x


# =====================================================================
# 評価器
# =====================================================================

def _to_decimal(x):
    if isinstance(x, Decimal):
        return x
    if isinstance(x, float):
        return Decimal(str(x))
    if isinstance(x, int):
        return Decimal(x)
    if isinstance(x, str):
        return Decimal(x)
    raise TypeError(f"Decimal変換不可: {type(x).__name__}")


class Evaluator:
    def __init__(self,
                 variable_resolver: Callable[[str], Any],
                 function_resolver: Optional[Callable[[str, List], Any]] = None):
        self.var = variable_resolver
        self.func = function_resolver or default_function_resolver

    def evaluate(self, source: str):
        ast = Parser.parse(source)
        if ast is None:
            return Decimal(0)
        return self._eval(ast)

    def _eval(self, node):
        if isinstance(node, Literal):
            return node.value
        if isinstance(node, GetVar):
            try:
                return self.var(node.name)
            except ExternalReferenceError:
                raise
            except ExpressionError:
                raise
            except Exception as e:
                raise ExpressionError(f"変数エラー: {node.name}", node.at, e)
        if isinstance(node, Call):
            try:
                evaluated_args = [self._eval(a) for a in node.args]
                return self.func(node.name, evaluated_args)
            except ExternalReferenceError:
                raise
            except ExpressionError:
                raise
            except Exception as e:
                raise ExpressionError(f"関数エラー: {node.name}", node.at, e)
        if isinstance(node, UnaryOp):
            v = self._eval(node.x)
            if node.op == '-':
                if isinstance(v, float):
                    return -v
                return -_to_decimal(v)
            raise ExpressionError(f"未知の単項演算子: {node.op}")
        if isinstance(node, IfExpr):
            c = self._eval(node.cond)
            cd = c if isinstance(c, float) else _to_decimal(c)
            cond_true = (cd != 0) if not isinstance(cd, float) else (cd != 0.0)
            return self._eval(node.x) if cond_true else self._eval(node.y)
        if isinstance(node, BinOp):
            return self._eval_binop(node)
        raise ExpressionError(f"未知のノード: {type(node).__name__}")

    def _eval_binop(self, node):
        op = node.op
        x = self._eval(node.x)
        y = self._eval(node.y)
        if op == '^':
            return self._eval_power(node, x, y)
        if op in ('*', '/', '+', '-'):
            return self._eval_arith(op, x, y)
        if op in ('<', '<=', '>', '>=', '==', '!='):
            return self._eval_compare(op, x, y)
        raise ExpressionError(f"未知の二項演算子: {op}")

    def _eval_power(self, node, x, y):
        # Parser.cs の useDecimal 特例を再現:
        # 10^X / X^2 / (x*x)^0.5 / (x*x)^(1/2)
        use_decimal = False
        if isinstance(node.x, Literal) and isinstance(node.x.value, Decimal) and node.x.value == Decimal(10):
            use_decimal = True
        if isinstance(node.y, Literal) and isinstance(node.y.value, Decimal) and node.y.value == Decimal(2):
            use_decimal = True
        if isinstance(node.x, BinOp) and node.x.op == '*':
            if isinstance(node.x.x, GetVar) and isinstance(node.x.y, GetVar) and node.x.x.name == node.x.y.name:
                if isinstance(node.y, Literal) and isinstance(node.y.value, Decimal) and node.y.value == Decimal('0.5'):
                    use_decimal = True
                elif isinstance(node.y, BinOp) and node.y.op == '/':
                    if (isinstance(node.y.x, Literal) and node.y.x.value == Decimal(1)
                            and isinstance(node.y.y, Literal) and node.y.y.value == Decimal(2)):
                        use_decimal = True

        xd = float(x) if not isinstance(x, str) else float(Decimal(x))
        yd = float(y) if not isinstance(y, str) else float(Decimal(y))
        # 負底→0
        r = math.pow(xd, yd) if xd > 0.0 else 0.0
        if use_decimal:
            return _to_decimal(Decimal(str(r)))
        return r

    def _eval_arith(self, op, x, y):
        # 文字列連結 (+ のみ)
        if op == '+' and (isinstance(x, str) or isinstance(y, str)):
            return str(x) + str(y)
        # double 混在は double
        if isinstance(x, float) or isinstance(y, float):
            xf = float(x) if not isinstance(x, float) else x
            yf = float(y) if not isinstance(y, float) else y
            if op == '+': return xf + yf
            if op == '-': return xf - yf
            if op == '*': return xf * yf
            if op == '/': return 0.0 if yf == 0.0 else xf / yf
        xd = _to_decimal(x); yd = _to_decimal(y)
        if op == '+': return xd + yd
        if op == '-': return xd - yd
        if op == '*': return xd * yd
        if op == '/': return Decimal(0) if yd == 0 else xd / yd
        raise ExpressionError(f"未知の算術演算子: {op}")

    def _eval_compare(self, op, x, y):
        if isinstance(x, float) or isinstance(y, float):
            xv = float(x) if not isinstance(x, float) else x
            yv = float(y) if not isinstance(y, float) else y
        elif isinstance(x, str) and isinstance(y, str):
            xv, yv = x, y
        else:
            xv = _to_decimal(x); yv = _to_decimal(y)
        cmp_ops = {
            '<': lambda a, b: a < b,
            '<=': lambda a, b: a <= b,
            '>': lambda a, b: a > b,
            '>=': lambda a, b: a >= b,
            '==': lambda a, b: a == b,
            '!=': lambda a, b: a != b,
        }
        return Decimal(1) if cmp_ops[op](xv, yv) else Decimal(0)


# =====================================================================
# 組み込み関数
# =====================================================================

def default_function_resolver(name: str, args: List):
    n = name.upper()
    if n in ('ROUND', 'ROUNDDOWN', 'UPPER'):
        if len(args) != 2:
            raise ExpressionError(f"{n}: 引数が2つ必要です。")
        x = args[0] if isinstance(args[0], Decimal) else _to_decimal(args[0]) if not isinstance(args[0], float) else Decimal(str(args[0]))
        d = int(_to_decimal(args[1]))
        # 桁指定: d=2 → 小数第2位、d=0 → 整数、d=-1 → 10の位
        quant = Decimal(1).scaleb(-d)
        if n == 'ROUND':
            return x.quantize(quant, rounding=ROUND_HALF_UP)
        if n == 'ROUNDDOWN':
            return x.quantize(quant, rounding=ROUND_DOWN)
        if n == 'UPPER':
            return x.quantize(quant, rounding=ROUND_UP)
    # INT/MIN/MAX (ギャップ#1, 2026-06-05)
    # 根拠: source_ref/sirius Being/Core/Math/CalcCommand.cs G_OperatorDefine
    #   int = x>0 ? Floor : Ceiling (ゼロ方向への切り詰め。INT(-3.7) = -3)
    #   min/max = 可変長引数の最小/最大
    if n == 'INT':
        if len(args) != 1:
            raise ExpressionError("INT: 引数が1つ必要です。")
        x = _to_decimal(args[0])
        return x.to_integral_value(rounding=ROUND_DOWN)  # ROUND_DOWN=ゼロ方向
    if n in ('MIN', 'MAX'):
        if not args:
            raise ExpressionError(f"{n}: 引数が1つ以上必要です。")
        vals = [_to_decimal(a) for a in args]
        return min(vals) if n == 'MIN' else max(vals)
    raise ExpressionError(f"未知の関数: {name}")


# =====================================================================
# KeisanHyo: 依存解決つき計算表
# =====================================================================

class UndefinedVariableError(ExpressionError):
    """strict_undefined=True の KeisanHyo で Value/Expression/ユーザ入力 が無い変数を参照した"""
    pass


class KeisanHyo:
    """
    KeisanItem群を遅延評価する計算表。

    使い方:
        items = [{'VarName': 'a', 'Value': 3}, {'VarName': 'b', 'Expression': 'a+1'}]
        hyo = KeisanHyo(items)
        hyo.value('b')                # → Decimal('4')
        hyo.set_input('a', 10)         # ユーザ入力で上書き
        hyo.value('b')                # → Decimal('11')
        hyo.evaluate('a*b')            # 任意式評価
        hyo.is_calculable('S1')        # 外部単価依存かどうか判定

    strict_undefined:
        False (デフォルト): Value/Expression/ユーザ入力 のどれもない変数は 0 として扱う
        True              : UndefinedVariableError を投げる
                            AutoSelectJoken 評価などで「未確定なら判定保留」したい場合に使う
    """

    def __init__(self, keisan_items: List[dict], strict_undefined: bool = False):
        self.items = {}
        for k in keisan_items:
            vn = k.get('VarName')
            if vn:
                self.items[vn] = k
        self._cache = {}
        self._evaluating = set()
        self._user_inputs = {}
        self._externals = set()
        self.strict_undefined = strict_undefined

    def set_external(self, name: str):
        """変数を外部依存(単価マスタ等・JSON単独で評価不能)として登録する。

        用途: SitsumonKind=113 (SetDaikaTankaToKeisan) で代価表行の単価が
        変数へ代入されるケース。値は単価マスタ由来なので @N と同じく
        ExternalReferenceError とし、0 で誤計算しないようにする。
        (仕様書: 歩掛JSON内部仕様書.md §4.6 P4 / ギャップ#5)
        """
        self._externals.add(name)
        self._cache.clear()

    def _resolve_var(self, name: str):
        if name.startswith('@'):
            raise ExternalReferenceError(f"外部単価参照: {name}")
        if name in self._externals:
            raise ExternalReferenceError(f"外部単価依存変数 (Sit113計上): {name}")
        if name in self._user_inputs:
            return self._user_inputs[name]
        if name in self._cache:
            return self._cache[name]
        if name in self._evaluating:
            raise ExpressionError(f"循環参照: {name}")
        k = self.items.get(name)
        if k is None:
            raise ExpressionError(f"未定義変数: {name}")

        self._evaluating.add(name)
        try:
            if 'Value' in k and not k.get('Expression'):
                v = _to_decimal(k['Value'])
            elif k.get('Expression'):
                # Expression 評価中は strict を解除 (内部の leaf 未確定変数も 0 で代用)
                # → 「leaf 直参照は strict、Expression 経由は緩和」の階層的扱い
                old_strict = self.strict_undefined
                self.strict_undefined = False
                try:
                    v = Evaluator(self._resolve_var).evaluate(k['Expression'])
                finally:
                    self.strict_undefined = old_strict
            else:
                if self.strict_undefined:
                    raise UndefinedVariableError(f"未確定変数 (Value/Expression/入力なし): {name}")
                # 非strict: ユーザ入力前提として 0 で代用
                v = Decimal(0)
            self._cache[name] = v
            return v
        finally:
            self._evaluating.discard(name)

    def value(self, name: str):
        return self._resolve_var(name)

    def set_input(self, name: str, value):
        self._user_inputs[name] = _to_decimal(value)
        # キャッシュをパージ (依存変数の連鎖再計算のため)
        self._cache.clear()

    def clear_inputs(self):
        self._user_inputs.clear()
        self._cache.clear()

    def evaluate(self, expression: str):
        return Evaluator(self._resolve_var).evaluate(expression)

    def is_calculable(self, name: str) -> bool:
        """その変数が外部単価依存なく計算可能か"""
        try:
            self._resolve_var(name)
            return True
        except ExternalReferenceError:
            return False
        except ExpressionError:
            return False
