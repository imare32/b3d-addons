"""L-system 重写引擎：模块串 → 模块串（纯 Python，**不 import bpy**）。

符号语义照 ABOP（*The Algorithmic Beauty of Plants*）与参考实现 ``.ref/lsystem`` 的 readme；
几何解释在 :mod:`gen_lsystem.turtle`，这里只回答一个「谁换成谁」的问题。三个概念分清楚：

* **模块**（:class:`Token`）：一个字符。带参数的写成 ``A(1,2)``，参数是数字或裸标识符；
* **规则**（:class:`Rule`）：``A(l,w) -> F(l)[+(45)B(mul(l,0.6),w)]``，可带**条件**
  ``gt(l,0.1)``；同一模块被多条规则命中时**随机**选一条（随机数按种子来，所以结果可复现）；
* **迭代**（:func:`iterate`）：并行重写 —— 每一轮把所有模块**同时**替换一遍，替换不了的留着。

参数表达式不走 Python ``eval``：这里有一个只认**函数式**写法的求值器，不认中缀运算符::

    rand 取值  add 加  sub 减  mul 乘  div 除  pow 幂  log 对数  sqrt 开方
    sin cos tan（弧度）  abs 取绝对值  floor 下取整  neg 取负  min max
    eq gt gteq lt（比较，返回 1 / 0）   get(i) / get(iter)（实例号 / 迭代号）

这样面板上填的东西永远不会执行到任意代码，``python -c`` 里跑出的结果也和 Blender 里一模一样。

与参考实现的两处**有意分歧**（都在 README 里写明）：

1. 参考实现的规则是按「字符串前缀」匹配的，左侧不带参数时连 ``A(1,2)`` 也匹配（消费掉一个
   字符，剩下的 ``(1,2)`` 当普通字符抄下去）；这里按**模块 + 参数个数**精确匹配；
2. ``%``（剪断分支）由 turtle 层执行，不在重写层吞字符 —— 两者在视觉上一致，但这里的语义
   更容易解释：``%`` 之后的 ``F`` 只前进不画线，直到本分支结束。
"""

from __future__ import annotations

import math
import random
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

__all__ = [
    "DEFAULT_MAX_SYMBOLS",
    "MAX_ITERATIONS",
    "LSystemError",
    "Rule",
    "Token",
    "evaluate",
    "iterate",
    "parse_rule",
    "parse_tokens",
]

#: 展开后模块数的上限。超过就报错，而不是把 Blender 卡死 —— 迭代数每 +1，指数型系统翻一倍。
DEFAULT_MAX_SYMBOLS = 400_000

#: 迭代数上限（面板的 ``max`` 也是它）。
MAX_ITERATIONS = 16

_IDENT = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")
_NUMBER = re.compile(r"(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?")

#: 默认随机源：不给 rng 时用它（种子 0，结果稳定）。
_RNG = random.Random(0)

#: 没有参数时共用的空环境。
_EMPTY_ENV: dict[str, object] = {}


class LSystemError(ValueError):
    """规则写错了 / 参数不是数字 / 展开太大 —— 都收成面板上看得懂的一句话。"""


@dataclass(frozen=True, slots=True)
class Token:
    """一个模块：模块名（单个字符）+ 已经求值好的参数。"""

    module: str
    params: tuple[float | str, ...] = ()

    def __str__(self) -> str:
        if not self.params:
            return self.module
        return f"{self.module}({', '.join(_format(value) for value in self.params)})"


@dataclass(frozen=True, slots=True)
class _RawToken:
    """规则右侧还没求值的模块（参数是表达式字符串）。"""

    module: str
    params: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Rule:
    """一条产生式。``pattern`` / ``result`` 是原文，``label`` 只用来报错。"""

    module: str
    parameters: tuple[str, ...]
    condition: str | None
    result: str
    tokens: tuple[_RawToken, ...]
    #: 右侧没有参数时预先求好的结果，随机重写时直接复用（省一遍求值）。
    plain: tuple[Token, ...] | None = None

    @property
    def label(self) -> str:
        """``A(l,w) -> F(l)``：报错时告诉用户是哪一条规则。"""
        head = self.module
        if self.parameters:
            head += f"({', '.join(self.parameters)})"
        if self.condition:
            head += f" :{self.condition}"
        return f"{head} -> {self.result}"


def _format(value: float | str) -> str:
    if isinstance(value, float):
        return f"{value:g}"
    return value


# --- 词法：模块串 -----------------------------------------------------------


def _match_paren(text: str, start: int) -> int:
    """``start`` 处的 ``(`` 对应的 ``)`` 下标（参数里可以套函数调用，所以按层数找）。"""
    depth = 0
    for index in range(start, len(text)):
        character = text[index]
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth == 0:
                return index
    raise LSystemError(f"括号不配对：{text[start : start + 40]!r}")


def _split_args(text: str) -> tuple[str, ...]:
    """按**第 0 层**逗号切参数（``mul(l,0.6)`` 里的逗号不算）。"""
    if not text.strip():
        return ()
    args: list[str] = []
    depth = 0
    start = 0
    for index, character in enumerate(text):
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
        elif character == "," and depth == 0:
            args.append(text[start:index].strip())
            start = index + 1
    args.append(text[start:].strip())
    return tuple(args)


def _scan(text: str) -> Iterator[tuple[str, tuple[str, ...]]]:
    """把一段模块串切成 ``(模块, 参数表达式)``；空白与杂散括号直接跳过。"""
    index = 0
    length = len(text)
    while index < length:
        character = text[index]
        index += 1
        if character.isspace() or character in ",)":
            continue
        if character == "(":  # 孤立的左括号：跳过整段
            index = _match_paren(text, index - 1) + 1
            continue
        params: tuple[str, ...] = ()
        if index < length and text[index] == "(":
            end = _match_paren(text, index)
            params = _split_args(text[index + 1 : end])
            index = end + 1
        yield character, params


# --- 表达式求值 -------------------------------------------------------------


def _lex_expression(text: str) -> list[str]:
    """表达式切词：数字 / 标识符 / ``( ) , + -`` / 裸字符串（``p(curve)`` 的 ``curve``）。"""
    tokens: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        character = text[index]
        if character.isspace():
            index += 1
            continue
        if character in "(),+-":
            tokens.append(character)
            index += 1
            continue
        match = _NUMBER.match(text, index)
        if match is not None:
            tokens.append(match.group())
            index = match.end()
            continue
        match = _IDENT.match(text, index)
        if match is not None:
            tokens.append(match.group())
            index = match.end()
            continue
        # 未知字符：一直吃到分隔符，当成一个裸字符串
        end = index
        while end < length and not (text[end].isspace() or text[end] in "(),+-"):
            end += 1
        tokens.append(text[index:end])
        index = end
    return tokens


def _num(value) -> float:
    """把求值结果当数字用；不是数字就抛一句能看懂的话。"""
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        raise LSystemError(f"参数 {value!r} 不是数字") from None


class _Parser:
    """递归下降：``值 := 数字 | 变量 | 函数(参数, ...) | (值) | -值``。"""

    __slots__ = ("tokens", "env", "rng", "iteration", "instance")

    def __init__(self, tokens, env, rng, iteration, instance):
        self.tokens = tokens
        self.env = env
        self.rng = rng
        self.iteration = iteration
        self.instance = instance

    def value(self, index: int):
        """读一个值，返回 ``(值, 下一个下标)``。"""
        if index >= len(self.tokens):
            raise LSystemError("表达式不完整")
        token = self.tokens[index]
        if token in ("+", "-"):
            value, index = self.value(index + 1)
            number = _num(value)
            return (number if token == "+" else -number), index
        if token == "(":
            value, index = self.value(index + 1)
            if index < len(self.tokens) and self.tokens[index] == ")":
                index += 1
            return value, index
        if token in (")", ","):
            raise LSystemError(f"表达式里多了一个 {token!r}")
        if _IDENT.fullmatch(token):
            if index + 1 < len(self.tokens) and self.tokens[index + 1] == "(":
                args, index = self.arguments(index + 2)
                return self.call(token, args), index
            if token in self.env:
                return self.env[token], index + 1
            try:
                return float(token), index + 1
            except ValueError:
                return token, index + 1
        try:
            return float(token), index + 1
        except ValueError:
            return token, index + 1

    def arguments(self, index: int):
        """从 ``index``（左括号之后）读到右括号，返回 ``(参数列表, 下一个下标)``。"""
        values: list[object] = []
        if index < len(self.tokens) and self.tokens[index] == ")":
            return values, index + 1
        while True:
            value, index = self.value(index)
            values.append(value)
            if index >= len(self.tokens):
                break
            token = self.tokens[index]
            if token == ",":
                index += 1
                continue
            if token == ")":
                index += 1
                break
            raise LSystemError(f"参数里多了一个 {token!r}")
        return values, index

    def call(self, name: str, args: Sequence[object]):
        """函数调用。函数表就是参考实现那一套，见模块说明。"""
        try:
            if name == "rand":
                return self.rng.uniform(_num(args[0]), _num(args[1]))
            if name == "add":
                return _num(args[0]) + _num(args[1])
            if name == "sub":
                return _num(args[0]) - _num(args[1])
            if name == "mul":
                return _num(args[0]) * _num(args[1])
            if name == "div":
                divisor = _num(args[1])
                if divisor == 0.0:
                    raise LSystemError("div() 的除数是 0")
                return _num(args[0]) / divisor
            if name == "pow":
                return math.pow(_num(args[0]), _num(args[1]))
            if name == "log":
                if len(args) > 1:
                    return math.log(_num(args[0]), _num(args[1]))
                return math.log(_num(args[0]))
            if name == "sqrt":
                return math.sqrt(_num(args[0]))
            if name == "sin":
                return math.sin(_num(args[0]))
            if name == "cos":
                return math.cos(_num(args[0]))
            if name == "tan":
                return math.tan(_num(args[0]))
            if name == "abs":
                return abs(_num(args[0]))
            if name == "floor":
                return math.floor(_num(args[0]))
            if name == "neg":
                return -_num(args[0])
            if name == "min":
                return min(_num(value) for value in args)
            if name == "max":
                return max(_num(value) for value in args)
            if name == "eq":
                return 1.0 if _num(args[0]) == _num(args[1]) else 0.0
            if name == "gt":
                return 1.0 if _num(args[0]) > _num(args[1]) else 0.0
            if name == "gteq":
                return 1.0 if _num(args[0]) >= _num(args[1]) else 0.0
            if name == "lt":
                return 1.0 if _num(args[0]) < _num(args[1]) else 0.0
            if name == "get":
                key = args[0] if args else ""
                text = key if isinstance(key, str) else _format(float(key))
                if text == "i":
                    return float(self.instance)
                if text == "iter":
                    return float(self.iteration)
                return 0.0
        except IndexError:
            raise LSystemError(f"{name}() 参数个数不够") from None
        except LSystemError:
            raise
        except (ArithmeticError, ValueError) as exc:
            raise LSystemError(f"{name}(): {exc}") from None
        raise LSystemError(f"未知函数 {name}()")


def evaluate(
    expression: str,
    env: dict[str, object] | None = None,
    *,
    rng: random.Random | None = None,
    iteration: int = 0,
    instance: int = 0,
):
    """求值一个参数表达式，返回 ``float`` 或裸字符串（``p(curve)`` 那种）。"""
    tokens = _lex_expression(expression)
    if not tokens:
        raise LSystemError("空表达式")
    parser = _Parser(tokens, env or _EMPTY_ENV, rng or _RNG, int(iteration), int(instance))
    value, index = parser.value(0)
    if index != len(tokens):
        raise LSystemError(f"表达式 {expression!r} 有多余内容：{' '.join(tokens[index:])}")
    return value


# --- 解析 -------------------------------------------------------------------


def parse_tokens(
    text: str,
    *,
    rng: random.Random | None = None,
    iteration: int = 0,
    instance: int = 0,
) -> list[Token]:
    """把模块串解析成 :class:`Token` 列表（参数在这里求值）。"""
    out: list[Token] = []
    for module, params in _scan(text):
        values = tuple(
            evaluate(param, None, rng=rng, iteration=iteration, instance=instance)
            for param in params
        )
        out.append(Token(module, values))
    return out


def parse_rule(pattern: str, result: str, condition: str | None = None) -> Rule:
    """解析一条规则。左侧只允许**一个**模块，参数名必须是标识符。

    :raises LSystemError: 左侧为空 / 不止一个模块 / 参数名不是标识符。
    """
    items = list(_scan(pattern))
    if not items:
        raise LSystemError(f"规则左侧是空的：{pattern!r}")
    if len(items) > 1:
        raise LSystemError(f"规则左侧只能写一个模块：{pattern!r}")
    module, raw_params = items[0]

    parameters: list[str] = []
    for name in raw_params:
        if not _IDENT.fullmatch(name):
            raise LSystemError(f"参数名必须是标识符：{name!r}（在规则 {pattern!r} 里）")
        parameters.append(name)

    raw_tokens = tuple(_RawToken(item_module, item_params) for item_module, item_params in _scan(result))
    plain = None
    if all(not token.params for token in raw_tokens):
        plain = tuple(Token(token.module) for token in raw_tokens)

    text = (condition or "").strip()
    return Rule(
        module=module,
        parameters=tuple(parameters),
        condition=text or None,
        result=result,
        tokens=raw_tokens,
        plain=plain,
    )


# --- 迭代 -------------------------------------------------------------------


def _truth(
    rule: Rule, env: dict[str, object], rng: random.Random, iteration: int, instance: int
) -> bool:
    """条件：非 0 即真；空条件恒真。"""
    if not rule.condition:
        return True
    try:
        value = evaluate(rule.condition, env, rng=rng, iteration=iteration, instance=instance)
    except LSystemError as exc:
        raise LSystemError(f"规则 {rule.label} 的条件出错：{exc}") from None
    try:
        return _num(value) != 0.0
    except LSystemError as exc:
        raise LSystemError(f"规则 {rule.label} 的条件不是数字") from exc


def _instantiate(
    rule: Rule, env: dict[str, object], rng: random.Random, iteration: int, instance: int
) -> tuple[Token, ...]:
    """把一条规则的右侧算成带数值的模块；没有参数的直接用预求好的那份。"""
    if rule.plain is not None:
        return rule.plain
    try:
        return tuple(
            Token(
                token.module,
                tuple(
                    evaluate(param, env, rng=rng, iteration=iteration, instance=instance)
                    for param in token.params
                ),
            )
            for token in rule.tokens
        )
    except LSystemError as exc:
        raise LSystemError(f"规则 {rule.label} 出错：{exc}") from None


def iterate(
    axiom: str,
    rules: Sequence[Rule] = (),
    iterations: int = 1,
    *,
    seed: int = 0,
    instance: int = 0,
    max_symbols: int = DEFAULT_MAX_SYMBOLS,
) -> list[Token]:
    """并行重写 ``iterations`` 轮，返回模块列表。

    :param axiom: 公理（起始模块串）。
    :param rules: 产生式；同一模块多条命中时按 ``seed`` 随机挑一条。
    :param iterations: 轮数（上限 :data:`MAX_ITERATIONS`）。
    :param seed: 随机种子（决定随机规则与 ``rand()`` / ``£``）。
    :param instance: ``get(i)`` 的返回值（单实例恒 0，留着与参考实现对齐）。
    :param max_symbols: 模块数上限，超了抛错而不是把机器拖死。
    :raises LSystemError: 迭代数太大 / 展开超过上限 / 规则里表达式出错。
    """
    iterations = int(iterations)
    if iterations < 0:
        raise LSystemError("迭代数不能是负数")
    if iterations > MAX_ITERATIONS:
        raise LSystemError(f"迭代数最多 {MAX_ITERATIONS}")

    rng = random.Random(int(seed))
    tokens = parse_tokens(axiom, rng=rng, instance=instance)
    if iterations == 0 or not rules:
        return tokens

    by_module: dict[str, list[Rule]] = {}
    for rule in rules:
        by_module.setdefault(rule.module, []).append(rule)

    for round_index in range(iterations):
        out: list[Token] = []
        for token in tokens:
            candidates = by_module.get(token.module)
            if not candidates:
                out.append(token)
                continue

            matches: list[tuple[Rule, dict[str, object]]] = []
            for rule in candidates:
                if len(rule.parameters) != len(token.params):
                    continue
                env: dict[str, object] = (
                    dict(zip(rule.parameters, token.params)) if rule.parameters else _EMPTY_ENV
                )
                if _truth(rule, env, rng, round_index, instance):
                    matches.append((rule, env))
            if not matches:
                out.append(token)
                continue

            rule, env = matches[0] if len(matches) == 1 else rng.choice(matches)
            out.extend(_instantiate(rule, env, rng, round_index, instance))
            if len(out) > max_symbols:
                raise LSystemError(
                    f"展开超过 {max_symbols:,} 个模块（第 {round_index + 1} 轮）——"
                    "把迭代数调小，或把规则写得更紧凑"
                )
        tokens = out
    return tokens
