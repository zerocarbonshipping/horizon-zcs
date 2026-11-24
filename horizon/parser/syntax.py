"""
The syntax of a .hor file.

Its text as plain records with their line numbers, which the rest of the parser gives meaning.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from lark import Lark, Token, Transformer, v_args
from lark.exceptions import UnexpectedCharacters, UnexpectedToken, VisitError
from lark.lexer import PatternStr

from horizon.exceptions import ParseError

if TYPE_CHECKING:
    from collections.abc import Iterable

    from lark.tree import Meta

# a value as the file writes it: text, or a list of values
type Text = str | list[Text]


@dataclass
class Entry:
    """`key = value`; a value is text or a list of values."""

    key: str
    value: Text
    line: int


@dataclass
class Call:
    """The one-line distribution call of a parameter, e.g. `= UNIFORM(0, 1)`."""

    name: str
    args: list[Text]
    line: int


@dataclass
class Comparison:
    """A condition of an `if` block as written: `token = value`, or `token in (values)` when among."""

    token: str
    values: list[Text]
    among: bool


@dataclass
class IfBlock:
    """An `if` block: its conditions and its entries."""

    conditions: list[Comparison]
    entries: list[Entry]
    line: int


@dataclass
class Block:
    """A parameter block."""

    kind: str
    name: str
    line: int
    call: Call | None = None
    entries: list[Entry] = field(default_factory=list)
    overrides: list[IfBlock] = field(default_factory=list)


@dataclass
class Rule:
    """An Include(...) or Exclude(...) rule of the Horizon block."""

    kind: str
    pairs: list[Entry]
    line: int


@dataclass
class HorizonBlock:
    """The Horizon block: settings and scenario rules."""

    entries: list[Entry]
    rules: list[Rule]
    line: int


@v_args(meta=True)
class _HorTransformer(Transformer[Token, list[Block | HorizonBlock]]):
    """Turn the parse tree into the records above."""

    def start(self, meta: Meta, blocks: list[Block | HorizonBlock]) -> list[Block | HorizonBlock]:
        return blocks

    def parameter(self, meta: Meta, children: list[Any]) -> Block:
        kind, name, *rest = children
        block = Block(str(kind), _unquote(name), meta.line)
        for part in rest:
            if isinstance(part, Call):
                block.call = part
            else:
                block.entries = [item for item in part if isinstance(item, Entry)]
                block.overrides = [item for item in part if isinstance(item, IfBlock)]

        return block

    def call(self, meta: Meta, children: list[Any]) -> Call:
        name, *args = children
        return Call(str(name).upper(), args, meta.line)

    def body(self, meta: Meta, items: list[Entry | IfBlock]) -> list[Entry | IfBlock]:
        return items

    def override(self, meta: Meta, children: list[Any]) -> IfBlock:
        conditions = [child for child in children if isinstance(child, Comparison)]
        return IfBlock(conditions, [child for child in children if isinstance(child, Entry)], meta.line)

    def equals(self, meta: Meta, children: list[Any]) -> Comparison:
        token, value = children
        return Comparison(str(token), [value], among=False)

    def among(self, meta: Meta, children: list[Any]) -> Comparison:
        token, *values = children
        return Comparison(str(token), values, among=True)

    def horizon(self, meta: Meta, items: list[Entry | Rule]) -> HorizonBlock:
        return HorizonBlock(
            [item for item in items if isinstance(item, Entry)], [item for item in items if isinstance(item, Rule)], meta.line
        )

    def include(self, meta: Meta, pairs: list[Entry]) -> Rule:
        return Rule("Include", pairs, meta.line)

    def exclude(self, meta: Meta, pairs: list[Entry]) -> Rule:
        return Rule("Exclude", pairs, meta.line)

    def entry(self, meta: Meta, children: list[Entry]) -> Entry:
        return children[0]

    def pair(self, meta: Meta, children: list[Any]) -> Entry:
        key, value = children
        return Entry(str(key), value, key.line)

    def number(self, meta: Meta, children: list[Token]) -> str:
        return str(children[0])

    def string(self, meta: Meta, children: list[Token]) -> str:
        return _unquote(children[0])

    def word(self, meta: Meta, children: list[Token]) -> str:
        return str(children[0])

    def value_list(self, meta: Meta, children: list[Text]) -> list[Text]:
        return children


def _unquote(token: Token) -> str:
    return str(token)[1:-1]


_PARSER = Lark.open_from_package(
    "horizon.parser", "grammar.lark", parser="lalr", propagate_positions=True, maybe_placeholders=False
)

# the text of each fixed-string terminal, e.g. LBRACE -> {
_LITERALS = {
    terminal.name: terminal.pattern.value for terminal in _PARSER.terminals if isinstance(terminal.pattern, PatternStr)
}


def _describe(terminal: str) -> str:
    """Name a terminal as a study author reads it: its text, or its name in words."""
    if terminal == "$END":
        return "end of file"

    if terminal in _LITERALS:
        return f"'{_LITERALS[terminal]}'"

    return {"WORD": "a name", "QUOTED_STRING": "a quoted string", "SIGNED_NUMBER": "a number", "KIND": "a parameter block"}.get(
        terminal, terminal.lower()
    )


def parse_text(text: str, file: str) -> list[Block | HorizonBlock]:
    """
    Parse the text of a .hor file into its blocks.

    Parameters
    ----------
    text : str
        Content of the file.
    file : str
        Name of the file in error messages.
    """
    try:
        tree = _PARSER.parse(text)
    except UnexpectedToken as error:
        got = _describe("$END") if error.token.type == "$END" else repr(str(error.token))
        raise _syntax_error(file, error.line, got, error.expected) from None
    except UnexpectedCharacters as error:
        raise _syntax_error(file, error.line, repr(error.char), error.allowed) from None

    try:
        return _HorTransformer().transform(tree)
    except VisitError as error:
        raise error.orig_exc from None


def _syntax_error(file: str, line: int, got: str, expected: Iterable[str]) -> ParseError:
    *others, last = sorted({_describe(name) for name in expected}) or ["nothing"]
    listed = f"{', '.join(others)} or {last}" if others else last
    return ParseError(f"unexpected {got}, expected {listed}", f"{file}:{line}")
