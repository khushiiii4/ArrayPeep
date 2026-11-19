"""Tests for arraypeep.lexer (roadmap Phase 3, tasks 3.1 and 3.2)."""
import unittest

from arraypeep.lexer import KEYWORDS, MiniLoopSyntaxError, Token, TokenKind, tokenize
from arraypeep.symbol_table import ArrayPeepError

K = TokenKind


def kinds(source: str) -> list[TokenKind]:
    return [t.kind for t in tokenize(source)]


def texts(source: str) -> list[str]:
    return [t.text for t in tokenize(source)[:-1]]


class TokenSetTests(unittest.TestCase):
    def test_keywords(self) -> None:
        for word, kind in {"array": K.ARRAY, "at": K.AT, "for": K.FOR, "to": K.TO,
                           "do": K.DO, "end": K.END}.items():
            self.assertEqual(kinds(word), [kind, K.EOF])
        self.assertEqual(set(KEYWORDS), {"array", "at", "for", "to", "do", "end"})

    def test_operators_and_punctuation(self) -> None:
        self.assertEqual(
            kinds("+ - * = [ ] ( ) , : ;"),
            [K.PLUS, K.MINUS, K.STAR, K.EQUALS, K.LBRACKET, K.RBRACKET, K.LPAREN,
             K.RPAREN, K.COMMA, K.COLON, K.SEMI, K.EOF],
        )

    def test_identifiers(self) -> None:
        for name in ["s", "A", "x1", "_tmp", "long_name_2", "T", "P", "Tx", "T1a", "ends", "format"]:
            with self.subTest(name=name):
                toks = tokenize(name)
                self.assertEqual((toks[0].kind, toks[0].text), (K.IDENT, name))

    def test_keywords_are_case_sensitive_and_whole_words(self) -> None:
        self.assertEqual(kinds("For End AT"), [K.IDENT, K.IDENT, K.IDENT, K.EOF])
        self.assertEqual(kinds("endx forx"), [K.IDENT, K.IDENT, K.EOF])
        self.assertEqual(kinds("end_ do1"), [K.IDENT, K.IDENT, K.EOF])

    def test_case_sensitive_identifiers(self) -> None:
        self.assertEqual(texts("a A"), ["a", "A"])

    def test_integer_literals(self) -> None:
        for text in ["0", "7", "120", "1000000", "007"]:
            toks = tokenize(text)
            self.assertEqual((toks[0].kind, toks[0].value), (K.INT, int(text)))

    def test_multi_digit_numbers_inside_expressions(self) -> None:
        self.assertEqual(texts("A[10,20]"), ["A", "[", "10", ",", "20", "]"])
        self.assertEqual(texts("x=123*45;"), ["x", "=", "123", "*", "45", ";"])

    def test_negative_literals_are_two_tokens(self) -> None:
        # A literal never carries a sign: '-' is its own token (language_spec.md Section 2).
        toks = tokenize("-5")
        self.assertEqual([t.kind for t in toks], [K.MINUS, K.INT, K.EOF])
        self.assertEqual(toks[1].value, 5)
        self.assertEqual(kinds("N[-5:5, -3:3]"),
                         [K.IDENT, K.LBRACKET, K.MINUS, K.INT, K.COLON, K.INT, K.COMMA,
                          K.MINUS, K.INT, K.COLON, K.INT, K.RBRACKET, K.EOF])
        self.assertEqual(kinds("a - -b"), [K.IDENT, K.MINUS, K.MINUS, K.IDENT, K.EOF])
        self.assertEqual(kinds("--"), [K.MINUS, K.MINUS, K.EOF])

    def test_adjacent_tokens_without_spaces(self) -> None:
        self.assertEqual(texts("a[i+1]=b;"), ["a", "[", "i", "+", "1", "]", "=", "b", ";"])
        self.assertEqual(texts("12ab"), ["12", "ab"])  # the parser rejects this, not the lexer

    def test_token_value_only_for_integers(self) -> None:
        with self.assertRaises(ValueError):
            tokenize("x")[0].value

    def test_empty_and_blank_sources(self) -> None:
        for src in ["", "   ", "\n\n", "\t \r\n", "# only a comment", "# c\n# d\n"]:
            with self.subTest(src=src):
                toks = tokenize(src)
                self.assertEqual([t.kind for t in toks], [K.EOF])

    def test_always_ends_with_exactly_one_eof(self) -> None:
        for src in ["", "x", "x = 1;", "# c"]:
            toks = tokenize(src)
            self.assertEqual(sum(t.kind is K.EOF for t in toks), 1)
            self.assertIs(toks[-1].kind, K.EOF)


class CommentTests(unittest.TestCase):
    def test_whole_line_comment(self) -> None:
        self.assertEqual(kinds("# hello\nx"), [K.IDENT, K.EOF])

    def test_comment_after_code(self) -> None:
        self.assertEqual(texts("s = s + 1;    # add one\nt = 2;"),
                         ["s", "=", "s", "+", "1", ";", "t", "=", "2", ";"])

    def test_comment_hides_everything_to_end_of_line(self) -> None:
        self.assertEqual(texts("x # y = 1; @ $ é\ny"), ["x", "y"])

    def test_hash_inside_comment_and_empty_comment(self) -> None:
        self.assertEqual(texts("#\n# # #\nx"), ["x"])

    def test_non_ascii_is_allowed_in_comments(self) -> None:
        toks = tokenize("# é中文 → café\nx = 1;")
        self.assertEqual([t.text for t in toks[:-1]], ["x", "=", "1", ";"])
        self.assertEqual(toks[0].line, 2)

    def test_comment_at_end_of_file_without_newline(self) -> None:
        self.assertEqual(kinds("x # end"), [K.IDENT, K.EOF])

    def test_comment_with_crlf(self) -> None:
        self.assertEqual(texts("x # c\r\ny"), ["x", "y"])


class PositionTests(unittest.TestCase):
    def test_line_and_column_are_one_based(self) -> None:
        toks = tokenize("s = 1;\n  t = 22;")
        got = [(t.text, t.line, t.col) for t in toks[:-1]]
        self.assertEqual(
            got,
            [("s", 1, 1), ("=", 1, 3), ("1", 1, 5), (";", 1, 6),
             ("t", 2, 3), ("=", 2, 5), ("22", 2, 7), (";", 2, 9)],
        )

    def test_end_column(self) -> None:
        toks = tokenize("abc 123")
        self.assertEqual((toks[0].col, toks[0].end_col), (1, 4))
        self.assertEqual((toks[1].col, toks[1].end_col), (5, 8))

    def test_eof_position(self) -> None:
        toks = tokenize("x\n")
        self.assertEqual((toks[-1].line, toks[-1].col), (2, 1))
        toks = tokenize("x = 1;")
        self.assertEqual((toks[-1].line, toks[-1].col), (1, 7))

    def test_tab_counts_as_one_column(self) -> None:
        toks = tokenize("\tx\t=\t1")
        self.assertEqual([(t.text, t.col) for t in toks[:-1]], [("x", 2), ("=", 4), ("1", 6)])

    def test_blank_lines_and_comments_advance_the_line(self) -> None:
        toks = tokenize("\n\n# c\n\nx")
        self.assertEqual((toks[0].line, toks[0].col), (5, 1))

    def test_position_after_a_comment_on_the_same_line_is_not_affected(self) -> None:
        toks = tokenize("a # comment\nb")
        self.assertEqual([(t.text, t.line, t.col) for t in toks[:-1]], [("a", 1, 1), ("b", 2, 1)])


class LineEndingTests(unittest.TestCase):
    def test_lf_and_crlf_give_the_same_tokens_and_positions(self) -> None:
        lf = "array A[1:2] at 0;\ns = 1;\n  s = s + 2;\n"
        crlf = lf.replace("\n", "\r\n")
        a = [(t.kind, t.text, t.line, t.col) for t in tokenize(lf)]
        b = [(t.kind, t.text, t.line, t.col) for t in tokenize(crlf)]
        self.assertEqual(a[:-1], b[:-1])  # EOF position may differ by the final line ending

    def test_crlf_line_numbers(self) -> None:
        toks = tokenize("a\r\nb\r\n\r\nc")
        self.assertEqual([(t.text, t.line, t.col) for t in toks[:-1]],
                         [("a", 1, 1), ("b", 2, 1), ("c", 4, 1)])

    def test_lone_cr_is_whitespace(self) -> None:
        toks = tokenize("a\rb")
        self.assertEqual([t.text for t in toks[:-1]], ["a", "b"])
        self.assertEqual({t.line for t in toks[:-1]}, {1})  # lines are counted by LF only

    def test_byte_order_mark_is_ignored(self) -> None:
        toks = tokenize("﻿x = 1;")
        self.assertEqual([(t.text, t.line, t.col) for t in toks[:-1]],
                         [("x", 1, 1), ("=", 1, 3), ("1", 1, 5), (";", 1, 6)])

    def test_bom_alone(self) -> None:
        self.assertEqual(kinds("﻿"), [K.EOF])

    def test_bom_only_ignored_at_the_start(self) -> None:
        with self.assertRaises(MiniLoopSyntaxError):
            tokenize("x ﻿")


class IllegalCharacterTests(unittest.TestCase):
    def assertIllegal(self, source: str, char_text: str, line: int, col: int) -> None:
        with self.assertRaises(MiniLoopSyntaxError) as ctx:
            tokenize(source)
        err = ctx.exception
        self.assertEqual(err.message, f"illegal character {char_text}")
        self.assertEqual((err.line, err.col), (line, col))

    def test_roadmap_style_examples(self) -> None:
        self.assertIllegal("s = 3 $ 4;", "'$'", 1, 7)  # language_spec.md Section 9
        self.assertIllegal("x = 1;\ny = @;", "'@'", 2, 5)

    def test_common_illegal_characters(self) -> None:
        for ch in "@$%^&!~`\\|{}<>?.\"'/":
            with self.subTest(ch=ch):
                self.assertIllegal(f"a {ch} b", f"'{ch}'", 1, 3)

    def test_non_ascii_outside_a_comment_is_illegal(self) -> None:
        self.assertIllegal("x = é;", "'é'", 1, 5)
        self.assertIllegal("café = 1;", "'é'", 1, 4)  # ASCII letters only in identifiers
        self.assertIllegal("x = ٣;", "'٣'", 1, 5)  # a non-ASCII digit is not a digit
        self.assertIllegal("x = − 1;", "'−'", 1, 5)  # unicode minus sign

    def test_control_characters_are_shown_escaped(self) -> None:
        with self.assertRaises(MiniLoopSyntaxError) as ctx:
            tokenize("x\x00")
        self.assertEqual(ctx.exception.message, "illegal character '\\x00'")
        self.assertEqual((ctx.exception.line, ctx.exception.col), (1, 2))

    def test_first_illegal_character_is_reported(self) -> None:
        self.assertIllegal("a @ b $ c", "'@'", 1, 3)

    def test_error_position_after_tabs_and_lines(self) -> None:
        self.assertIllegal("a\n\t@", "'@'", 2, 2)

    def test_formatting(self) -> None:
        with self.assertRaises(MiniLoopSyntaxError) as ctx:
            tokenize("x = 1;\ny = @;")
        self.assertEqual(
            ctx.exception.format("prog.ml"),
            "prog.ml: line 2, col 5: syntax error: illegal character '@'",
        )
        self.assertEqual(str(ctx.exception), "illegal character '@'")

    def test_error_class_hierarchy(self) -> None:
        with self.assertRaises(ArrayPeepError):
            tokenize("@")


class TokenRecordTests(unittest.TestCase):
    def test_token_is_immutable_and_comparable(self) -> None:
        tok = Token(K.IDENT, "x", 1, 1)
        with self.assertRaises(Exception):
            tok.text = "y"  # type: ignore[misc]
        self.assertEqual(tok, Token(K.IDENT, "x", 1, 1))
        self.assertNotEqual(tok, Token(K.IDENT, "x", 1, 2))

    def test_real_program(self) -> None:
        src = "array A[1:10, 1:20] at 1000;\nfor i = 1 to 3 do\n  s = s + A[i, 2];\nend\n"
        self.assertEqual(
            texts(src),
            ["array", "A", "[", "1", ":", "10", ",", "1", ":", "20", "]", "at", "1000", ";",
             "for", "i", "=", "1", "to", "3", "do", "s", "=", "s", "+", "A", "[", "i", ",", "2",
             "]", ";", "end"],
        )


if __name__ == "__main__":
    unittest.main()
