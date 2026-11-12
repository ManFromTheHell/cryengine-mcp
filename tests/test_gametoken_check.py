"""gametoken_check against synthetic levels: the control (a correct library) is clean, every broken one is named."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import sys
import tempfile

import gametoken_check as G

HERE = _paths.ROOT
failed = []


N = [0]


def check(name, ok, detail=""):
    N[0] += 1
    print("  %s %s %s" % ("ok  " if ok else "FAIL", name.ljust(100), "" if ok else detail))
    if not ok:
        failed.append(name)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf8") as f:
        f.write(text)


def ids(res):
    return sorted(f["id"] for f in res["findings"] if f["id"] != "G011")      # G011 (a loose folder is not read by a Release level load) is checked on its own below


with tempfile.TemporaryDirectory() as tmp:
    lvl = os.path.join(tmp, "Assets", "levels", "t")
    write(os.path.join(lvl, "GameTokens", "quest.xml"),
          '<GameTokensLibrary Name="Quest"><Token Name="TomMet" Type="Bool" Value="0" LocalOnly="0"/><Token Name="Coins" Type="Int" Value="3"/><Token Name="Pos" Type="Vec3" Value="1,2,3"/></GameTokensLibrary>')
    res = G.check("t", tmp)
    check("control: a correct library has no findings but the loose-folder warning, and its tokens are listed", ids(res) == [] and res["libraries"] == {"Quest": ["TomMet", "Coins", "Pos"]}, res)
    check("a loose GameTokens folder is flagged G011 (Release level loads do not read GameData.pak)", any(f["id"] == "G011" for f in res["findings"]), res["findings"])

    write(os.path.join(lvl, "layers", "main.lyr"), '<Node gametoken_Token="Quest.TomMet"/><Node gametoken_Token="Quest.Typo"/>')
    res = G.check("t", tmp)
    check("a reference to a token no library defines is G010 (and only that one)", ids(res) == ["G010"] and any("Quest.Typo" in x["message"] for x in res["findings"] if x["id"] == "G010"), res["findings"])
    write(os.path.join(lvl, "layers", "main.lyr"), "")

    cases = [
        ("wrong root tag", '<Library Name="Q"><Token Name="A" Type="Int" Value="1"/></Library>', ["G002"]),
        ("no library Name", '<GameTokensLibrary><Token Name="A" Type="Int" Value="1"/></GameTokensLibrary>', ["G003"]),
        ("lower-case type (the loader is case-sensitive)", '<GameTokensLibrary Name="Q"><Token Name="A" Type="bool" Value="1"/></GameTokensLibrary>', ["G006"]),
        ("Void type", '<GameTokensLibrary Name="Q"><Token Name="A" Type="Void" Value=""/></GameTokensLibrary>', ["G006"]),
        ("token without a Name", '<GameTokensLibrary Name="Q"><Token Type="Int" Value="1"/></GameTokensLibrary>', ["G005"]),
        ("duplicate keeps the first", '<GameTokensLibrary Name="Q"><Token Name="A" Type="Int" Value="1"/><Token Name="A" Type="Int" Value="2"/></GameTokensLibrary>', ["G009"]),
        ("a value that is not a number", '<GameTokensLibrary Name="Q"><Token Name="A" Type="Int" Value="three"/></GameTokensLibrary>', ["G008"]),
        ("a space in a token name", '<GameTokensLibrary Name="Q"><Token Name="A B" Type="Int" Value="1"/></GameTokensLibrary>', ["G004"]),
        ("malformed XML", '<GameTokensLibrary Name="Q"><Token', ["G001"]),
    ]
    os.remove(os.path.join(lvl, "GameTokens", "quest.xml"))
    for label, xml, want in cases:
        write(os.path.join(lvl, "GameTokens", "bad.xml"), xml)
        res = G.check("t", tmp)
        check("%s: %s" % (label, want), ids(res) == want, res["findings"])
        os.remove(os.path.join(lvl, "GameTokens", "bad.xml"))

    res = G.check("nolevel", tmp)
    check("a level that does not exist is an error, not an empty pass", "error" in res, res)

_levels = os.path.join(G.PROJECT, "Assets", "levels")
_real_levels = sorted(d for d in os.listdir(_levels) if os.path.isdir(os.path.join(_levels, d))) if os.path.isdir(_levels) else []
if _real_levels:
    _lv = "ashvale" if "ashvale" in _real_levels else _real_levels[0]
    real = G.check(_lv)
    check("the real level '%s' answers (no GameTokens folder is a fact, not a fault)" % _lv, "error" not in real and real["errors"] == 0, real)
else:
    print("  --   SKIPPED: the project has no level under Assets/levels, so there is no real level to check")

print("\n%d checks, %d failed" % (N[0], len(failed)))
sys.exit(1 if failed else 0)
