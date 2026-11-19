"""Recursive-descent parser that builds the AST (mirrors the paper's G[A], G[V]).

Grammar (docs/language_spec.md Section 3)::

    program      = { item } ;
    item         = declaration | statement ;
    declaration  = "array" IDENT "[" bound ":" bound { "," bound ":" bound } "]"
                   "at" signed_int ";" ;
    statement    = assignment | for_loop ;
    assignment   = variable "=" expr ";" ;
    variable     = IDENT | IDENT "[" subscripts "]" ;
    subscripts   = expr { "," expr } ;
    for_loop     = "for" IDENT "=" signed_int "to" signed_int "do"
                   { statement } "end" ;
    expr         = term { ( "+" | "-" ) term } ;
    term         = unary { "*" unary } ;
    unary        = "-" unary | primary ;
    primary      = INT | IDENT | IDENT "[" subscripts "]" | "(" expr ")" ;
    signed_int   = [ "-" ] INT ;

The paper's left-recursive ``elist -> elist , E`` is the loop over
comma-separated subscripts in ``_subscripts``.

Syntax errors (``MiniLoopSyntaxError``) stop at the first one. Position rule
(design decision 43): an *unexpected token* is reported at the start of that
token; a *missing token* (``expected ';'``), and an unexpected end of file, are
reported just after the end of the previous token, with `` before end of
file`` appended in the latter case. The parser knows nothing about quadruples.
"""
from .ast_nodes import (
    ArrayDecl,
    ArrayRef,
    Assign,
    BinOp,
    Expr,
    ForLoop,
    Number,
    Program,
    ScalarRef,
    Stmt,
    UnaryMinus,
)
from .lexer import MiniLoopSyntaxError, Token, TokenKind, tokenize

K = TokenKind

_SHOWN = {
    K.ARRAY: "'array'", K.AT: "'at'", K.FOR: "'for'", K.TO: "'to'", K.DO: "'do'",
    K.END: "'end'", K.PLUS: "'+'", K.MINUS: "'-'", K.STAR: "'*'", K.EQUALS: "'='",
    K.LBRACKET: "'['", K.RBRACKET: "']'", K.LPAREN: "'('", K.RPAREN: "')'",
    K.COMMA: "','", K.COLON: "':'", K.SEMI: "';'",
}

BOUND_MESSAGE = "array bounds must be integer constants (variable-bound arrays are not supported)"
BASE_MESSAGE = "the base address of an array must be an integer constant"
LOOP_BOUND_MESSAGE = "loop bounds must be integer constants"


class Parser:
    """Parser over a token list (as returned by ``tokenize``)."""

    def __init__(self, tokens: list[Token]) -> None:
        if not tokens or tokens[-1].kind is not K.EOF:
            raise ValueError("token list must end with an EOF token")
        self._tokens = tokens
        self._pos = 0

    # -- token access and errors ---------------------------------------------

    @property
    def _tok(self) -> Token:
        return self._tokens[self._pos]

    def _advance(self) -> Token:
        tok = self._tokens[self._pos]
        if tok.kind is not K.EOF:
            self._pos += 1
        return tok

    def _error_at_token(self, message: str) -> MiniLoopSyntaxError:
        """Unexpected token: reported at its start (just after the last token at end of file)."""
        if self._tok.kind is K.EOF:
            return self._error_after_previous(message)
        return MiniLoopSyntaxError(message, self._tok.line, self._tok.col)

    def _error_after_previous(self, message: str) -> MiniLoopSyntaxError:
        """Missing token: reported just after the previous token."""
        if self._tok.kind is K.EOF:
            message += " before end of file"
        if self._pos == 0:
            return MiniLoopSyntaxError(message, self._tok.line, self._tok.col)
        prev = self._tokens[self._pos - 1]
        return MiniLoopSyntaxError(message, prev.line, prev.end_col)

    def _expect(self, kind: TokenKind, shown: str | None = None) -> Token:
        if self._tok.kind is kind:
            return self._advance()
        raise self._error_after_previous(f"expected {shown or _SHOWN[kind]}")

    # -- program, declarations, statements -----------------------------------

    def parse_program(self) -> Program:
        """Parse the whole token list; the program may be empty."""
        items = []
        while self._tok.kind is not K.EOF:
            items.append(self._item())
        return Program(tuple(items), line=1, col=1)

    def _item(self):
        if self._tok.kind is K.ARRAY:
            return self._declaration()
        return self._statement()

    def _declaration(self) -> ArrayDecl:
        kw = self._advance()  # 'array'
        name = self._expect(K.IDENT, "an array name")
        self._expect(K.LBRACKET)
        bounds = []
        while True:
            lo = self._signed_int(BOUND_MESSAGE)
            self._expect(K.COLON)
            hi = self._signed_int(BOUND_MESSAGE)
            bounds.append((lo, hi))
            if self._tok.kind is K.COMMA:
                self._advance()
                continue
            break
        self._expect(K.RBRACKET)
        self._expect(K.AT)
        base = self._signed_int(BASE_MESSAGE)
        self._expect(K.SEMI)
        return ArrayDecl(name.text, tuple(bounds), base, line=kw.line, col=kw.col)

    def _statement(self) -> Stmt:
        kind = self._tok.kind
        if kind is K.FOR:
            return self._for_loop()
        if kind is K.IDENT:
            return self._assignment()
        if kind is K.ARRAY:
            raise self._error_at_token("array declarations are only allowed at the top level")
        if kind is K.END:
            raise self._error_at_token("unexpected 'end' (no matching 'for')")
        raise self._error_at_token("expected a statement")

    def _for_loop(self) -> ForLoop:
        kw = self._advance()  # 'for'
        var = self._expect(K.IDENT, "a loop variable name")
        self._expect(K.EQUALS)
        lo = self._signed_int(LOOP_BOUND_MESSAGE)
        self._expect(K.TO)
        hi = self._signed_int(LOOP_BOUND_MESSAGE)
        self._expect(K.DO)
        body = []
        while self._tok.kind is not K.END:
            if self._tok.kind is K.EOF:
                raise self._error_after_previous("expected 'end'")
            body.append(self._statement())
        self._advance()  # 'end' (no semicolon follows it)
        return ForLoop(var.text, lo, hi, tuple(body), line=kw.line, col=kw.col)

    def _assignment(self) -> Assign:
        name = self._advance()  # IDENT
        if self._tok.kind is K.LBRACKET:
            target: ScalarRef | ArrayRef = self._array_ref(name)
        else:
            target = ScalarRef(name.text, line=name.line, col=name.col)
        self._expect(K.EQUALS)
        value = self._expr()
        self._expect(K.SEMI)
        return Assign(target, value, line=name.line, col=name.col)

    def _signed_int(self, message: str) -> int:
        sign = 1
        if self._tok.kind is K.MINUS:
            self._advance()
            sign = -1
        if self._tok.kind is not K.INT:
            if self._tok.kind is K.EOF:
                raise self._error_after_previous("expected an integer constant")
            raise self._error_at_token(message)
        return sign * self._advance().value

    # -- expressions -----------------------------------------------------------

    def _expr(self) -> Expr:
        left = self._term()
        while self._tok.kind in (K.PLUS, K.MINUS):
            op = self._advance()
            right = self._term()
            left = BinOp(op.text, left, right, line=op.line, col=op.col)
        return left

    def _term(self) -> Expr:
        left = self._unary()
        while self._tok.kind is K.STAR:
            op = self._advance()
            right = self._unary()
            left = BinOp("*", left, right, line=op.line, col=op.col)
        return left

    def _unary(self) -> Expr:
        if self._tok.kind is K.MINUS:
            op = self._advance()
            return UnaryMinus(self._unary(), line=op.line, col=op.col)
        return self._primary()

    def _primary(self) -> Expr:
        tok = self._tok
        if tok.kind is K.INT:
            self._advance()
            return Number(tok.value, line=tok.line, col=tok.col)
        if tok.kind is K.IDENT:
            self._advance()
            if self._tok.kind is K.LBRACKET:
                return self._array_ref(tok)
            return ScalarRef(tok.text, line=tok.line, col=tok.col)
        if tok.kind is K.LPAREN:
            self._advance()
            inner = self._expr()
            self._expect(K.RPAREN)
            return inner
        raise self._error_at_token("expected an expression")

    def _array_ref(self, name: Token) -> ArrayRef:
        """``name`` has been consumed and the current token is ``[``: parse the subscript list."""
        self._advance()  # '['
        subscripts = [self._expr()]  # elist -> i [ E
        while self._tok.kind is K.COMMA:  # elist -> elist , E  as a loop
            self._advance()
            subscripts.append(self._expr())
        self._expect(K.RBRACKET)
        return ArrayRef(name.text, tuple(subscripts), line=name.line, col=name.col)


def parse_tokens(tokens: list[Token]) -> Program:
    """Parse a token list (ending with ``EOF``) into a ``Program``."""
    return Parser(tokens).parse_program()


def parse(source: str) -> Program:
    """Lex and parse MiniLoop ``source``.

    Raises ``MiniLoopSyntaxError`` (with ``line`` and ``col``) on the first error.
    """
    return parse_tokens(tokenize(source))
