"""packc lexer: turns .pack source text into a token stream.

Hand-written scanner (no regex engine needed at runtime beyond trivial
character classes). Tracks line/column for precise error messages.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Token:
    kind: str      # IDENT, STRING, NUMBER, BOOL, LBRACE, ...
    value: object  # str for IDENT/STRING, int|float for NUMBER, bool for BOOL
    line: int
    col: int


class LexError(Exception):
    def __init__(self, message: str, line: int, col: int):
        super().__init__(f"{line}:{col}: {message}")
        self.line = line
        self.col = col


_SIMPLE = {
    "{": "LBRACE", "}": "RBRACE",
    "[": "LBRACK", "]": "RBRACK",
    "(": "LPAREN", ")": "RPAREN",
    ":": "COLON", ",": "COMMA", "?": "QMARK",
}


def lex(text: str, filename: str = "<string>") -> list[Token]:
    tokens: list[Token] = []
    i, n = 0, len(text)
    line, col = 1, 1

    def advance(k: int = 1) -> None:
        nonlocal i, line, col
        for _ in range(k):
            if i < n and text[i] == "\n":
                line += 1
                col = 1
            else:
                col += 1
            i += 1

    while i < n:
        c = text[i]
        # whitespace
        if c in " \t\r\n":
            advance()
            continue
        # comments: # to end of line
        if c == "#":
            while i < n and text[i] != "\n":
                advance()
            continue
        # strings with \" \\ \n \t escapes
        if c == '"':
            sline, scol = line, col
            advance()
            buf: list[str] = []
            closed = False
            while i < n:
                ch = text[i]
                if ch == "\\":
                    advance()
                    if i >= n:
                        break
                    esc = text[i]
                    buf.append({"n": "\n", "t": "\t", '"': '"', "\\": "\\"}.get(esc, esc))
                    advance()
                elif ch == '"':
                    advance()
                    closed = True
                    break
                elif ch == "\n":
                    raise LexError("unterminated string literal", sline, scol)
                else:
                    buf.append(ch)
                    advance()
            if not closed:
                raise LexError("unterminated string literal", sline, scol)
            tokens.append(Token("STRING", "".join(buf), sline, scol))
            continue
        # numbers: -?digits(.digits)?
        if c.isdigit() or (c == "-" and i + 1 < n and text[i + 1].isdigit()):
            sline, scol = line, col
            j = i
            if text[j] == "-":
                j += 1
            while j < n and text[j].isdigit():
                j += 1
            is_float = False
            if j < n and text[j] == "." and j + 1 < n and text[j + 1].isdigit():
                is_float = True
                j += 1
                while j < n and text[j].isdigit():
                    j += 1
            raw = text[i:j]
            advance(j - i)
            tokens.append(Token("NUMBER", float(raw) if is_float else int(raw), sline, scol))
            continue
        # multi-char operators: -> >= <= == !=
        two = text[i:i + 2]
        if two == "->":
            tokens.append(Token("ARROW", "->", line, col)); advance(2); continue
        if two == ">=":
            tokens.append(Token("GTE", ">=", line, col)); advance(2); continue
        if two == "<=":
            tokens.append(Token("LTE", "<=", line, col)); advance(2); continue
        if two == "==":
            tokens.append(Token("EQEQ", "==", line, col)); advance(2); continue
        if two == "!=":
            tokens.append(Token("BANGEQ", "!=", line, col)); advance(2); continue
        if c == "=":
            tokens.append(Token("EQ", "=", line, col)); advance(); continue
        if c == ">":
            tokens.append(Token("GT", ">", line, col)); advance(); continue
        if c == "<":
            tokens.append(Token("LT", "<", line, col)); advance(); continue
        # identifiers (may contain interior hyphens: check-availability)
        if c.isalpha() or c == "_":
            sline, scol = line, col
            j = i
            while j < n and (text[j].isalnum() or text[j] == "_"):
                j += 1
            # absorb hyphenated continuations, but never the '->' arrow
            while j < n and text[j] == "-" and j + 1 < n and text[j + 1] not in ">" and (text[j + 1].isalnum() or text[j + 1] == "_"):
                j += 1
                while j < n and (text[j].isalnum() or text[j] == "_"):
                    j += 1
            word = text[i:j]
            advance(j - i)
            if word == "true":
                tokens.append(Token("BOOL", True, sline, scol))
            elif word == "false":
                tokens.append(Token("BOOL", False, sline, scol))
            else:
                tokens.append(Token("IDENT", word, sline, scol))
            continue
        if c in _SIMPLE:
            tokens.append(Token(_SIMPLE[c], c, line, col)); advance(); continue
        raise LexError(f"unexpected character {c!r}", line, col)

    tokens.append(Token("EOF", None, line, col))
    return tokens
