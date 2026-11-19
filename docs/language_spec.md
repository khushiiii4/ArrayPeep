# MiniLoop language specification

Phase 1 specification. No lexer or parser exists yet; this document defines what they must accept.

Provenance tags (same meaning as in `design_decisions.md`): **[PAPER]** from the research paper, **[SIMPLIFIED]** a simplification for this student project, **[OUR EXTENSION]** our own design choice where the roadmap or paper is silent.

Related documents: `quad_spec.md` (intermediate code, memory, levels, outputs), `hand_examples.md` (worked examples), `design_decisions.md`.

## 1. Overview

MiniLoop is a tiny loop language. A program is a list of **array declarations**, **assignments** and **counted `for` loops**. All integers are mathematical integers (no overflow). Source files use the extension `.ml`.

A complete program:

```
# sum of a 1-D array
array V[0:9] at 100;
s = 0;
for i = 0 to 9 do
  s = s + V[i];
end
```

## 2. Lexical rules

| Item | Rule |
|---|---|
| Source encoding [OUR EXTENSION] | UTF-8, read as text. A leading byte-order mark is ignored. Non-ASCII characters are allowed **only inside comments**; anywhere else they are an illegal character. |
| Line endings [OUR EXTENSION] | `\n` (LF) and `\r\n` (CRLF) are both accepted and equivalent (this repository is edited on Windows, and Git may convert line endings). A lone `\r` is treated as whitespace. Lines are counted by `\n`. |
| Line and column numbers | Both 1-based. The column counts characters from the start of the line; a **tab counts as 1 column** [OUR EXTENSION]. |
| Whitespace | Spaces, tabs, `\r` and newlines separate tokens and are otherwise ignored. |
| Comment | `#` to the end of the line. A comment may follow code on the same line. |
| Keywords (reserved) | `array` `at` `for` `to` `do` `end` |
| Identifier | An **ASCII** letter (`A-Z`, `a-z`) or `_`, then ASCII letters, digits or `_`. Case-sensitive (`A` and `a` differ). Must not be a keyword. [OUR EXTENSION: ASCII only] |
| Integer literal | One or more decimal digits, e.g. `0`, `7`, `120`. A literal never contains a sign; see unary minus below. |
| Operators | `+` `-` `*` `=` |
| Punctuation | `[` `]` `(` `)` `,` `:` `;` |

Rules explained:

- **Case-sensitive:** `s` and `S` are different names.
- **Reserved generated names [OUR EXTENSION]:** an identifier matching the pattern `[TP][0-9]+` (the letter `T` or `P` followed by **one or more** digits: `T1`, `P12`, `T01`) is reserved for compiler-generated temporaries (`quad_spec.md`, Section 2) and is rejected as a user name. `T` and `P` alone, and names such as `T1a` or `Tx`, are ordinary identifiers. This keeps listings unambiguous.
- **Negative numbers:** the lexer produces `-` and a digit-string as separate tokens. Where the grammar needs a *signed integer constant* (array bounds, `BASE`, loop bounds) the parser accepts an optional leading `-` (Section 3, `signed_int`). In expressions, `-` is the unary minus operator.
- **Illegal character:** any other character (for example `@`, `$`, `{`) is a syntax error reported with line and column (Section 9).

## 3. Grammar

EBNF. Quoted text is literal; `{ x }` means zero or more; `[ x ]` means optional. Tokens are described in Section 2.

```
program      = { item } ;
item         = declaration | statement ;

declaration  = "array" IDENT "[" bound ":" bound { "," bound ":" bound } "]"
               "at" signed_int ";" ;
bound        = signed_int ;

statement    = assignment | for_loop ;
assignment   = variable "=" expr ";" ;
variable     = IDENT | IDENT "[" subscripts "]" ;
subscripts   = expr { "," expr } ;
for_loop     = "for" IDENT "=" signed_int "to" signed_int "do"
               { statement } "end" ;

expr         = term { ( "+" | "-" ) term } ;
term         = unary { "*" unary } ;
unary        = "-" unary | primary ;
primary      = INT
             | IDENT
             | IDENT "[" subscripts "]"
             | "(" expr ")" ;

signed_int   = [ "-" ] INT ;
```

Notes on the grammar:

- **Subscript list [PAPER-derived].** The paper rewrites the grammar of subscripted variables so that the array name and its first subscript are read together (roadmap Section 1.4: `V -> elist ]`, `elist -> elist , E | i [ E`), so that the translator knows the array while reducing the list. The roadmap summarises this as the paper's `G[A]` (assignment) and `G[V]` (variable); the paper's grammar text itself is not reproduced here. Our parser reads the same language with an **iterative** list (`subscripts = expr { "," expr }`), which is equivalent to the left-recursive rule without left recursion (roadmap Phase 3.4).
- **Precedence, from tightest to loosest:** parentheses; unary `-`; `*`; binary `+` and `-`. So `a + b * c` means `a + (b * c)` and `(a + b) * c` is different. [SIMPLIFIED]
- **Associativity:** `+`, `-`, `*` are left-associative: `a - b - c` means `(a - b) - c`.
- **Unary minus** may be repeated: `- - x` is legal and equals `x`.
- **No semicolon after `end`.** A `for` loop ends with `end` alone.
- **Declarations only at the top level** (not inside a loop body) [SIMPLIFIED]; they may be interleaved with statements, but an array must be declared earlier in the text than its first use (Section 5).
- **An empty loop body** (`for i = 1 to 3 do end`) and an **empty program** are legal.
- **No** `if`, `while`, functions, division, comparison, strings, floating point, `+=`, or a print statement.

## 4. Constructs, with examples

### 4.1 Static array declaration [PAPER scope; syntax SIMPLIFIED]

```
array A[l1:u1, ..., ln:un] at BASE;
```

Declares an array of integers with `n` dimensions (1 to 4). Dimension `k` has lower bound `lk` and upper bound `uk`; both are inclusive. `BASE` is the memory address of the first element `A[l1, ..., ln]`.

```
array V[0:9] at 100;              # 1-D, 10 elements, addresses 100..109
array A[1:10, 1:20] at 1000;      # 2-D, 200 elements, addresses 1000..1199
array N[-5:5, -3:3] at 200;       # negative bounds are allowed, 77 elements
array M[0:3, 0:4, 0:5] at 0;      # 3-D, 120 elements
```

Rules:

- Bounds and `BASE` are **integer constants** (literals, optionally negative). A variable or an expression is not allowed: `array B[1:n] at 0;` is a syntax error. This is the paper's **static array** case; variable-bound arrays are out of scope (the paper excludes them too). [PAPER scope]
- Each upper bound must be at least its lower bound (`u >= l`), so every dimension has at least one value. `array B[5:4] at 0;` is a semantic error. A dimension with `l = u` (a single value) is legal.
- At most 4 dimensions. [SIMPLIFIED]
- Storage is **row-major**, one memory cell per element, ascending addresses (`quad_spec.md`, Section 5). Row-major is the only layout. [PAPER]
- Element type is always integer.
- `BASE` is explicit so that tests are deterministic. [SIMPLIFIED] It may be negative.
- Two arrays must not share an address (Section 5).

### 4.2 Scalar variables [SIMPLIFIED]

A scalar is any identifier that is not an array name and not a loop variable. Scalars need **no declaration**: they come into existence at their first mention. Every scalar holds the integer **0** until it is first assigned [OUR EXTENSION; the roadmap does not say]. Scalars live in named cells outside array memory.

```
s = 0;          # good practice: initialise explicitly
s = s + 1;
```

### 4.3 Assignment

```
V = E;
```

`V` is either a scalar `x` or an array element `A[E1, ..., En]`; `E` is an expression. The assignment evaluates `E`, then stores it.

```
x = 5;
A[i, j] = x * 2;
B[i] = B[i] + 1;       # reads and writes the same element
```

Assignment is a statement, not an expression; `x = y = 3;` is a syntax error.

### 4.4 Expressions

| Form | Meaning | Example |
|---|---|---|
| Integer literal | A constant | `42` |
| Scalar variable | Its current value | `s` |
| Loop variable | The current value of the loop counter | `i` |
| Array element | `A[E1, ..., En]` reads memory | `A[i, j+1]` |
| `E + E`, `E - E`, `E * E` | Integer addition, subtraction, multiplication | `a + b * 2` |
| `( E )` | Grouping | `(a + b) * c` |
| `- E` | Unary minus | `-x`, `-(a + b)` |

Evaluation order [OUR EXTENSION; needed so that address traces are well defined]:

1. Operands are evaluated **left to right** (the left operand completely, then the right operand).
2. Subscripts of an array element are evaluated left to right, then the element is read.
3. For `A[...] = E;` the right-hand side `E` is evaluated **first**, then the subscripts of the target, then the store happens.

Example: in `Z[i,j] = Z[i,j] + X[i,k] * Y[k,j];` the memory accesses occur in the order: read `Z[i,j]`, read `X[i,k]`, read `Y[k,j]`, write `Z[i,j]`.

**Any expression may be a subscript.** `A[2*i + 1, j - 1]`, `V[W[i]]` and `A[i*j, 1]` are all legal MiniLoop. Only *linear* subscripts are optimised in later phases (Section 8); the others still compile and run correctly. [PAPER scope]

### 4.5 Counted `for` loop [SIMPLIFIED]

```
for v = lo to hi do
  statements
end
```

- `lo` and `hi` are **integer constants** (literals, optionally negative).
- The loop variable `v` takes the values `lo, lo+1, ..., hi` in order: the **step is +1**. The body runs `max(0, hi - lo + 1)` times.
- If `lo > hi` the body runs **zero times** (legal, not an error). If `lo = hi` it runs once.
- `v` is **read-only** inside the loop: assigning to it is a semantic error.
- Loops may be **nested to any depth**; a nested loop must use a **different** variable name from every loop enclosing it.
- After the loop ends, `v` is not accessible (Section 5).

```
for i = 1 to 3 do
  for j = 1 to 2 do
    s = s + i * j;
  end
end
```

```
for i = 5 to 1 do        # zero iterations: legal
  s = s + 1;
end
```

Why constants and step 1 matter: they let the optimiser know, at compile time, the exact set of values every loop variable takes, which is what makes linear subscripts (Eq. 4 of the paper) analysable. [PAPER scope]

### 4.6 Comments

```
# a whole-line comment
s = s + 1;    # a comment after code
```

A comment runs from `#` to the end of the line and has no effect.

## 5. Semantic rules

Checked after parsing. Each violation is a **semantic error reported with its line number** (Section 9).

| # | Rule | Bad example | Message (see Section 9) |
|---|---|---|---|
| S1 | An array must be declared before its first use, and only declared arrays may be subscripted. | `Q[1] = 2;` with no `array Q` | `array 'Q' is not declared` |
| S2 | The number of subscripts must equal the array's number of dimensions (the paper's `DIM` check). | `A[i]` for a 2-D array `A` | `array 'A' has 2 dimensions but 1 subscript(s) given` |
| S3 | An array name must always be used with subscripts. | `s = A + 1;` | `array 'A' used without subscripts` |
| S4 | No array may be declared twice. | two `array A[...]` lines | `array 'A' is already declared` |
| S5 | Every upper bound must be >= its lower bound. | `array B[5:4] at 0;` | `upper bound 4 is less than lower bound 5 in dimension 1 of array 'B'` |
| S6 | An array has 1 to 4 dimensions. | `array B[0:1,0:1,0:1,0:1,0:1] at 0;` | `array 'B' has 5 dimensions; at most 4 are supported` |
| S7 | Two arrays must not share any memory address. | `A` at 1000..1199 and `B[1:20] at 1100;` | `array 'B' (addresses 1100..1119) overlaps array 'A' (addresses 1000..1199)` |
| S8 | A loop variable must not be assigned anywhere. | `for i = 1 to 3 do i = 5; end` | `loop variable 'i' cannot be assigned` |
| S9 | A nested loop must not reuse the name of an enclosing loop variable. | `for i ... do for i ... do` | `loop variable 'i' is already used by an enclosing loop` |
| S10 | A name that is a loop variable somewhere in the program is reserved for loop variables: it cannot also be an array name or an ordinary scalar. [OUR EXTENSION] | `i = 0;` outside any loop, and `for i = ...` elsewhere | `'i' is used as a loop variable and as an ordinary name` |
| S11 | A loop variable may be read only inside its own loop. [OUR EXTENSION] | `s = i;` after the loop over `i` | `loop variable 'i' used outside its loop` |
| S12 | Reserved generated names (pattern `[TP][0-9]+`) cannot be used. [OUR EXTENSION] | `T1 = 0;` | `'T1' is reserved for compiler temporaries` |

**Precedence of rules [OUR EXTENSION].** One offending construct may violate several rules (for example `i = 0;` outside any loop violates both S8 and S10; `s = i;` after the loop over `i` violates both S10 and S11). Only the **first violated rule in the order S1, S2, ..., S12** is reported for that construct. A name that is declared as an array anywhere in the program is an array name everywhere: a plain use without subscripts, even **before** the declaration (`A = 1; array A[1:2] at 0;`), is reported by S3.

Notes:

- Sequential (non-nested) loops **may** reuse the same variable name; `1d_sum.ml` does so with `i`.
- S10 and S11 exist because the roadmap only says "v cannot be assigned inside" and "nested loops do not reuse a variable name"; it does not say what a loop variable means after its loop or whether it can share a name with a scalar. Making loop variables local and reserved keeps them unambiguous for the optimiser. This is a stricter rule than the roadmap states and is recorded as a design decision.
- Bounds containing a non-literal produce a **syntax** error (the grammar only accepts `signed_int`), with a hint that variable-bound arrays are not supported.

## 6. Restrictions (summary)

- Array bounds and `BASE`: integer constants.
- Loop bounds: integer constants; step +1.
- Loop variables cannot be modified inside their loops.
- Static arrays only; row-major only; 1 to 4 dimensions; element type integer.
- Control flow: counted loops only.
- Operators: `+`, `-`, `*`, unary `-`, parentheses. No division.

## 7. Example programs

Five specification examples are in `examples/`. They are not executed yet (no parser exists); each was checked by hand against the grammar of Section 3.

| File | Shows | Roadmap case |
|---|---|---|
| `1d_sum.ml` | 1-D array, non-zero lower bound, fill loop then sum loop | T02 |
| `2d_traversal.ml` | 2-D row-major traversal | T03 |
| `linear_subscripts.ml` | linear subscripts `2*i+1`, `j-1` | T04 |
| `3d_traversal.ml` | 3-D array, three nested loops | T05 |
| `matrix_multiply.ml` | three arrays, innermost `k` loop, `Z[i,j]` independent of `k` | T11 |

Expected address sequences for the first two are in `hand_examples.md`, Sections 5 and 6.

## 8. Which subscripts are optimised later [PAPER scope]

An access is optimisable only if every subscript is a **linear function of the enclosing loop variables with integer constant coefficients**: `i`, `2*i + 1`, `3 - j`, `(i + 1) * 4`, `i + j`, `5`. Not linear: `i * j` (product of loop variables), a scalar that is not a loop variable (its value is unknown at compile time), an array element inside a subscript. Non-linear accesses fall back to level L1 and still run correctly. (Details in `quad_spec.md`, Section 7.)

## 9. Error policy

Every error stops processing of the program, prints **exactly one message** to the error output and gives a non-zero exit code. Exit codes [OUR EXTENSION]:

| Code | Meaning |
|---|---|
| 0 | Success (and every requested verification is PASS) |
| 1 | The input file cannot be read, a syntax or semantic error was found, **or the command line is invalid** (unknown option, missing file argument, bad `--level` value) |
| 2 | Run-time error while executing the program |
| 3 | Verification FAIL (two levels disagree, or a non-baseline level fails where the baseline did not; `quad_spec.md`, Section 10) |
| 4 | Internal error: the compiler produced ill-formed code or hit an unexpected condition (a bug in the tool, not in the user's program) |

Command-line usage errors must use code 1, not the default of Python's `argparse` (which exits with 2 and would collide with "run-time error"); the Phase 10 driver overrides this. [OUR EXTENSION]

Message layouts (`<file>` is the path given on the command line):

| Kind | Layout | Example |
|---|---|---|
| Syntax error | `<file>: line L, col C: syntax error: <message>` | `prog.ml: line 4, col 12: syntax error: expected ';'` |
| Illegal character | same layout | `prog.ml: line 2, col 7: syntax error: illegal character '@'` |
| Semantic error | `<file>: line L: semantic error: <message>` | `prog.ml: line 6: semantic error: array 'Q' is not declared` |
| Run-time error | `runtime error: <message>` (see `quad_spec.md`, Section 10) | `runtime error: quadruple 7 ...` |
| Internal error | `internal error: <message>` | `internal error: T3 read before assignment` |

**Run-time and internal messages carry no `<file>:` prefix and no source line** [OUR EXTENSION]: they refer to a quadruple of the generated listing, not to a source position. They are the only messages that do not start with the file name. Each message is one line, written to standard error.

Rules:

- **Syntax errors** report the **line and column** of the error position. Only the first syntax error is reported. The position is: for an **unexpected token**, the start of that token; for a **missing token** (for example a missing `;`, `]` or `do`), the position **just after the end of the previous token**, on the previous token's line; for an **unexpected end of file**, just after the last token. [OUR EXTENSION: roadmap Phase 3 only shows the message form]
- **Semantic errors** report the **line** of the offending construct (for a multi-line statement, the line where it starts). Semantic checking stops at the **first** violation in source order; only that one message is printed (see the rule precedence in Section 5).
- **Undeclared array:** `array 'Q' is not declared` (S1).
- **Wrong number of subscripts:** `array 'A' has 2 dimensions but 1 subscript(s) given` (S2).
- **Invalid memory access** is detected only at run time and is a run-time error (`quad_spec.md`, Sections 4.3 and 10).

Syntax error examples:

| Program fragment | Error |
|---|---|
| `  s = s + 1` (two-space indent, no `;`) at line 4, with `end` on line 5 | `line 4, col 12: syntax error: expected ';'` (the position is just after the last token `1`, not the `end` on line 5) |
| `A[i, j = 1;` | `syntax error: expected ']'` |
| `array B[1:n] at 0;` | `syntax error: array bounds must be integer constants (variable-bound arrays are not supported)` |
| `for i = 1 to 3` then no `do` | `syntax error: expected 'do'` |
| `s = 3 $ 4;` | `syntax error: illegal character '$'` |
| `for i = 1 to 3 do` and the file ends | `syntax error: expected 'end' before end of file` |
