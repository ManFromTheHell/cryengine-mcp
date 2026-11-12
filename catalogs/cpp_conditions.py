#!/usr/bin/env python3
"""A small C preprocessor, just enough to answer: "is this line compiled into a Release build, and into a Profile build?"

The engine decides most of its build differences in CryCommon/CryCore/Project/ProjectDefines.h (ENABLE_PROFILING_CODE, ENABLE_STATOSCOPE, CVARS_WHITELIST ... are
defined there from _RELEASE and a few options) and then wraps code in `#if ENABLE_...`. Reading only `#if !defined(_RELEASE)` (what cvar_catalog did first) misses
every variable behind such a feature macro: e_StatoscopeFilenameUseDatagroups is registered in the Profile editor, has no flag that says "dev only", and does not
exist in the Release game (measured by the cvar_probe job). This module closes that gap by evaluating the conditions.

Three-valued: an expression is True, False, or None (cannot tell: it depends on a macro defined somewhere this module does not look, such as a CMake option).
A None never turns into a claim.

    defs = defines_from(open(".../ProjectDefines.h").read(), release=True)      # {macro: value} after the header ran for a Release build
    evaluate("defined(ENABLE_STATOSCOPE) && !defined(_RELEASE)", defs)           # -> False
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import re

# Windows desktop, 64-bit: what the editor, the Launcher and our packages are. CryPlatformDefines.h defines ONLY the active platform's macros (to 1) and leaves every
# other CRY_PLATFORM_* undefined, so `defined(CRY_PLATFORM_ORBIS)` is false and `#if CRY_PLATFORM_ORBIS` is 0.
PLATFORM = {"CRY_PLATFORM_WINDOWS": "1", "CRY_PLATFORM_DESKTOP": "1", "CRY_PLATFORM_WINAPI": "1", "CRY_PLATFORM_X64": "1",
            "_WIN32": "1", "_WIN64": "1", "WIN32": "1", "WIN64": "1", "_MSC_VER": "1930", "__cplusplus": "201703"}
# macros that are simply not defined for the engine modules of a standard Windows build (CMake does not pass them)
ABSENT = {"CRY_PLATFORM_MOBILE", "CRY_PLATFORM_CONSOLE", "CRY_PLATFORM_DURANGO", "CRY_PLATFORM_ORBIS", "CRY_PLATFORM_POSIX", "CRY_PLATFORM_ANDROID", "CRY_PLATFORM_IOS",
          "CRY_PLATFORM_MAC", "CRY_PLATFORM_LINUX", "CRY_PLATFORM_APPLE", "CRY_PLATFORM_ARM", "CRY_PLATFORM_NEON", "CRY_PLATFORM_UNKNOWNCPU", "CRY_PLATFORM_32BIT",
          "_RELEASE", "_PROFILE", "_DEBUG", "DEBUG", "__clang__", "__GNUC__", "DEDICATED_SERVER", "RESOURCE_COMPILER", "PERFORMANCE_BUILD", "CRY_IS_MONOLITHIC_BUILD", "ANDROID_OBB",
          "USE_RUNTIME_CVAR_OVERRIDES", "RELEASE_LOGGING_OFF", "STANDALONE_PHYSICS", "TARGET_OS_IPHONE", "TARGET_OS_MAC", "_DURANGO", "_XBOX_ONE", "_ORBIS", "__ORBIS__",
          "ANDROID", "__ANDROID__", "__linux__", "__linux"}

_TOKEN = re.compile(r"\s*(defined|0[xX][0-9a-fA-F]+[uUlL]*|\d+[uUlL]*|[A-Za-z_]\w*|&&|\|\||==|!=|<=|>=|<<|>>|[-+*/%<>!()~&|^?:,]|.)")


def _tokens(expr):
    expr = re.sub(r"/\*.*?\*/", " ", expr)
    expr = expr.split("//")[0]
    return [m.group(1) for m in _TOKEN.finditer(expr) if m.group(1).strip()]


class _Parser:
    """Recursive descent over the tokens; every method returns True/False/None for truth, or an int/None for a value."""

    def __init__(self, toks, defs, strict):
        self.t, self.i, self.defs, self.strict = toks, 0, defs, strict

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else None

    def take(self):
        tok = self.peek()
        self.i += 1
        return tok

    def ident_value(self, name):
        if name in self.defs:
            v = self.defs[name].strip()
            if v == "":
                return 1
            if re.match(r"^-?\d+$", v):
                return int(v)
            if re.match(r"^0[xX][0-9a-fA-F]+$", v):
                return int(v, 16)
            if v in self.defs and v != name:
                return self.ident_value(v)
            return None                      # defined as something we cannot evaluate (a function-like macro, an expression)
        return 0 if (self.strict or name in ABSENT or name in getattr(self.defs, "known", ())) else None

    def expr(self):
        v = self.or_()
        if self.peek() == "?":               # c ? a : b
            self.take()
            a = self.expr()
            if self.peek() == ":":
                self.take()
            b = self.expr()
            return None if v is None else (a if v else b)
        return v

    def or_(self):
        vals = [self.and_()]
        if self.peek() != "||":
            return vals[0]                   # no logical operator: keep the VALUE (a parenthesised (-7) must stay -7, not become True)
        while self.peek() == "||":
            self.take()
            vals.append(self.and_())
        truths = [None if v is None else bool(v) for v in vals]
        if any(t is True for t in truths):
            return True
        return None if any(t is None for t in truths) else (False if len(vals) > 1 else vals[0])

    def and_(self):
        vals = [self.cmp_()]
        if self.peek() != "&&":
            return vals[0]
        while self.peek() == "&&":
            self.take()
            vals.append(self.cmp_())
        truths = [None if v is None else bool(v) for v in vals]
        if any(t is False for t in truths):
            return False
        return None if any(t is None for t in truths) else (True if len(vals) > 1 else vals[0])

    # C operator precedence, tightest first: * / %  >  + -  >  << >>  >  < > <= >=  >  == !=  >  &  >  ^  >  |   (&& and || are and_ / or_ above)
    def _level(self, ops, sub, fn):
        a = sub()
        while self.peek() in ops:
            op = self.take()
            b = sub()
            if a is None or b is None:
                a = None
            else:
                try:
                    a = fn(op, a, b)
                except (ValueError, OverflowError):
                    a = None
        return a

    @staticmethod
    def _arith(op, a, b):
        if op == "*":
            return a * b
        if op in ("/", "%"):
            if not b:
                return None
            q = abs(a) // abs(b)                 # C truncates toward zero; Python floors
            if (a < 0) != (b < 0):
                q = -q
            return q if op == "/" else a - q * b
        return a + b if op == "+" else a - b

    def mul(self):
        return self._level(("*", "/", "%"), self.unary, self._arith)

    def add(self):
        return self._level(("+", "-"), self.mul, self._arith)

    def shift(self):
        return self._level(("<<", ">>"), self.add, lambda op, a, b: (a << b) if op == "<<" else (a >> b))

    def rel(self):
        return self._level(("<", ">", "<=", ">="), self.shift, lambda op, a, b: int({"<": a < b, ">": a > b, "<=": a <= b, ">=": a >= b}[op]))

    def eq(self):
        return self._level(("==", "!="), self.rel, lambda op, a, b: int((a == b) if op == "==" else (a != b)))

    def bitand(self):
        return self._level(("&",), self.eq, lambda op, a, b: a & b)

    def bitxor(self):
        return self._level(("^",), self.bitand, lambda op, a, b: a ^ b)

    def cmp_(self):                              # kept name: the loosest of the non-logical levels (bitwise |)
        return self._level(("|",), self.bitxor, lambda op, a, b: a | b)

    def unary(self):
        tok = self.peek()
        if tok == "!":
            self.take()
            v = self.unary()
            return None if v is None else int(not v)
        if tok == "~":
            self.take()
            v = self.unary()
            return None if v is None else ~v
        if tok == "-":
            self.take()
            v = self.unary()
            return None if v is None else -v
        if tok == "(":
            self.take()
            v = self.expr()
            if self.peek() == ")":
                self.take()
            return v
        if tok == "defined":
            self.take()
            paren = self.peek() == "("
            if paren:
                self.take()
            name = self.take()
            if paren and self.peek() == ")":
                self.take()
            if name in self.defs:
                return 1
            return 0 if (self.strict or name in ABSENT or name in getattr(self.defs, "known", ())) else None
        self.take()
        if tok is None:
            return None
        if re.match(r"^0[xX]", tok):
            return int(re.sub(r"[uUlL]+$", "", tok), 16)
        if re.match(r"^\d", tok):
            return int(re.sub(r"[uUlL]+$", "", tok))
        if re.match(r"^[A-Za-z_]", tok):
            if self.peek() == "(":           # a function-like macro or __has_include(...): skip its arguments, value unknown
                depth = 0
                while self.peek() is not None:
                    c = self.take()
                    depth += (c == "(") - (c == ")")
                    if depth == 0:
                        break
                return None
            return self.ident_value(tok)
        return None


def evaluate(expr, defs, strict=False):
    """True / False / None for a preprocessor expression. strict=True follows C exactly (an unknown identifier is 0); the default says None."""
    p = _Parser(_tokens(expr), defs, strict)
    v = p.expr()
    return None if v is None else bool(v)


class Defs(dict):
    """{macro: value} plus `known`: every macro name the processed header #defines somewhere. One of those that is not defined at the end IS undefined
    (ProjectDefines.h #undefs ENABLE_STATOSCOPE and defines it again only when profiling code is on); one the header only tests stays unknown."""
    known = frozenset()


_DIRECTIVE = re.compile(r"^\s*#\s*(\w+)\s*(.*)$")


def defines_from(text, release, base=None):
    """Run a header (ProjectDefines.h) through #if/#define/#undef for a Release (_RELEASE defined) or a Profile (not defined) build and return the macros it leaves
    defined: {name: value}. Conditions this module cannot decide take the branch C would take with the unknown names undefined (strict), and the header's own
    results are then only as good as those names; the platform and the two config macros are set explicitly."""
    defs = Defs(PLATFORM)
    defs.update(base or {})
    known = set()
    if release:
        defs["_RELEASE"] = ""
    else:
        defs["_PROFILE"] = ""
    lines = text.replace("\\\n", " ").splitlines()
    stack = []                               # (parent_active, any_branch_taken, this_branch_active)
    active = True
    for line in lines:
        m = _DIRECTIVE.match(line)
        if not m:
            continue
        d, rest = m.group(1), m.group(2).strip()
        if d == "define":                             # only what the header itself defines: a macro it merely tests or #undefs (USE_BROFILER, INCLUDE_SCALEFORM_SDK: CMake provides them) stays unknown
            known.add(re.match(r"\w+", rest).group(0) if re.match(r"\w+", rest) else "")
        if d in ("if", "ifdef", "ifndef"):
            cond = {"if": lambda: evaluate(rest, defs, strict=True), "ifdef": lambda: rest.split()[0] in defs, "ifndef": lambda: rest.split()[0] not in defs}[d]() if active else False
            stack.append((active, bool(cond), bool(cond)))
            active = active and bool(cond)
        elif d == "elif":
            parent, taken, _cur = stack.pop()
            cond = (not taken) and parent and evaluate(rest, defs, strict=True)
            stack.append((parent, taken or bool(cond), bool(cond)))
            active = parent and bool(cond)
        elif d == "else":
            parent, taken, _cur = stack.pop()
            stack.append((parent, True, not taken))
            active = parent and not taken
        elif d == "endif":
            if stack:
                parent, _taken, _cur = stack.pop()
                active = parent
        elif active and d == "define":
            mm = re.match(r"^(\w+)(\([^)]*\))?\s*(.*)$", rest)
            if mm:
                defs[mm.group(1)] = mm.group(3).strip() if not mm.group(2) else "<function-like>"
        elif active and d == "undef":
            defs.pop(rest.split()[0], None)
    defs.known = frozenset(known)
    return defs


def condition_terms(lines):
    """For each line, the list of (expression, wanted) that must all hold for the line to be compiled: `#if A` -> (A, True); after `#elif B`: (A, False), (B, True);
    after `#else`: (A, False), (B, False). `ifdef X` is (defined(X), True)."""
    stack, out = [], []
    for line in lines:
        m = re.match(r"^\s*#\s*(if|ifdef|ifndef|elif|else|endif)\b(.*)$", line)
        if m:
            kind, rest = m.group(1), re.split(r"//|/\*", m.group(2))[0].strip()
            if kind == "if":
                stack.append({"prior": [], "cur": rest})
            elif kind == "ifdef":
                stack.append({"prior": [], "cur": "defined(%s)" % rest})
            elif kind == "ifndef":
                stack.append({"prior": [], "cur": "!defined(%s)" % rest})
            elif kind == "elif" and stack:
                top = stack[-1]
                top["prior"].append(top["cur"])
                top["cur"] = rest
            elif kind == "else" and stack:
                top = stack[-1]
                top["prior"].append(top["cur"])
                top["cur"] = None
            elif kind == "endif" and stack:
                stack.pop()
        terms = []
        for chain in stack:
            terms.extend((p, False) for p in chain["prior"] if p is not None)
            if chain["cur"] is not None:
                terms.append((chain["cur"], True))
        out.append(terms)
    return out


def build_state(terms, defs):
    """Are the terms satisfied under `defs`? True / False / None (cannot tell)."""
    unknown = False
    for expr, want in terms:
        v = evaluate(expr, defs)
        if v is None:
            unknown = True
        elif v != want:
            return False
    return None if unknown else True
