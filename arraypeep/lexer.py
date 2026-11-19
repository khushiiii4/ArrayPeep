"""Lexer: converts MiniLoop source text into tokens with line and column.

Rules (docs/language_spec.md Section 2):

* Whitespace separates tokens. ``\\n`` (LF) and ``\\r\\n`` (CRLF) both end a
  line; a lone ``\\r`` is whitespace. A leading byte-order mark is ignored.
* ``#`` starts a comment that runs to the end of the line. Comments may
  contain any character (non-ASCII included); they produce no tokens.
* Keywords: ``array at for to do end``. Identifiers: an ASCII letter or ``_``
  followed by ASCII letters, digits or ``_``. Integer literals: ASCII digits
  only. A literal never carries a sign: ``-`` is always its own token and the
  parser decides between unary minus and a signed constant.
* Operators ``+ - * =``; punctuation ``[ ] ( ) , : ;``.
* Line and column numbers are 1-based; a tab counts as one column.
* Any other character is a syntax error (``illegal character``) with its
  position.

The token list always ends with one ``EOF`` token.
"""
from dataclasses import dataclass
from enum import Enum, auto

from .symbol_table import ArrayPeepError


class TokenKind(Enum):
    """Kinds of token."""

    ARRAY = auto()
    AT = auto()
    FOR = auto()
    TO = auto()
    DO = auto()
    END = auto()
    IDENT = auto()
    INT = auto()
    PLUS = auto()
    MINUS = auto()
    STAR = auto()
    EQUALS = auto()
    LBRACKET = auto()
    RBRACKET = auto()
    LPAREN = auto()
    RPAREN = auto()
    COMMA = auto()
    COLON = auto()
    SEMI = auto()
    EOF = auto()


KEYWORDS: dict[str, TokenKind] = {
    "array": TokenKind.ARRAY,
    "at": TokenKind.AT,
    "for": TokenKind.FOR,
    "to": TokenKind.TO,
    "do": TokenKind.DO,
    "end": TokenKind.END,
}

SYMBOLS: dict[str, TokenKind] = {
    "+": TokenKind.PLUS,
    "-": TokenKind.MINUS,
    "*": TokenKind.STAR,
    "=": TokenKind.EQUALS,
    "[": TokenKind.LBRACKET,
    "]": TokenKind.RBRACKET,
    "(": TokenKind.LPAREN,
    ")": TokenKind.RPAREN,
    ",": TokenKind.COMMA,
    ":": TokenKind.COLON,
    ";": TokenKind.SEMI,
}

_DIGITS = "0123456789"


@dataclass(frozen=True)
class Token:
    """One token: its kind, the source text, and the 1-based position of its first character."""

    kind: TokenKind
    text: str
    line: int
    col: int

    @property
    def value(self) -> int:
        """The integer value of an ``INT`` token."""
        if self.kind is not TokenKind.INT:
            raise ValueError(f"{self.kind.name} token has no integer value")
        return int(self.text)

    @property
    def end_col(self) -> int:
        """Column just after the last character (tokens never span lines)."""
        return self.col + len(self.text)


class MiniLoopSyntaxError(ArrayPeepError):
    """A lexical or syntax error at a source position (``line`` and ``col``, both 1-based)."""

    def __init__(self, message: str, line: int, col: int) -> None:
        super().__init__(message, line)
        self.col = col

    def format(self, filename: str) -> str:
        """``<file>: line L, col C: syntax error: <message>`` (language_spec.md Section 9)."""
        return f"{filename}: line {self.line}, col {self.col}: syntax error: {self.message}"


def _is_letter(ch: str) -> bool:
    return ("a" <= ch <= "z") or ("A" <= ch <= "Z") or ch == "_"


def _show_char(ch: str) -> str:
    """The character as shown in an error message."""
    return f"'{ch}'" if ch.isprintable() else repr(ch)


def tokenize(source: str) -> list[Token]:
    """Split ``source`` into tokens, ending with an ``EOF`` token.

    Raises ``MiniLoopSyntaxError`` for an illegal character.
    """
    tokens: list[Token] = []
    i = 1 if source.startswith("﻿") else 0  # the byte-order mark is not a column
    n = len(source)
    line, col = 1, 1
    while i < n:
        ch = source[i]
        if ch == "\n":
            i, line, col = i + 1, line + 1, 1
        elif ch in " \t\r":
            i, col = i + 1, col + 1
        elif ch == "#":
            while i < n and source[i] != "\n":  # the comment, up to (not including) the newline
                i, col = i + 1, col + 1
        elif _is_letter(ch):
            start = i
            while i < n and (_is_letter(source[i]) or source[i] in _DIGITS):
                i += 1
            text = source[start:i]
            tokens.append(Token(KEYWORDS.get(text, TokenKind.IDENT), text, line, col))
            col += i - start
        elif ch in _DIGITS:
            start = i
            while i < n and source[i] in _DIGITS:
                i += 1
            tokens.append(Token(TokenKind.INT, source[start:i], line, col))
            col += i - start
        elif ch in SYMBOLS:
            tokens.append(Token(SYMBOLS[ch], ch, line, col))
            i, col = i + 1, col + 1
        else:
            raise MiniLoopSyntaxError(f"illegal character {_show_char(ch)}", line, col)
    tokens.append(Token(TokenKind.EOF, "", line, col))
    return tokens
