"""Offline checks for the export/save target-path helpers (no Origin needed).

Everything here was measured against the live bridge first (2026-10-12): Origin's
export filter rewrites '#' into a numbering digit, Win32 strips trailing spaces
and dots out of path components, and a path with no real filename used to turn
into a dotfile named ".png" inside the plugin's own install folder.
"""

import os
import shutil
import stat
import sys

import originlab as ol

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.join(os.environ.get("TEMP", HERE), "origin_bridge_path_checks")

PASSED = 0
FAILED = []


def ok(label, cond, detail=""):
    global PASSED
    if cond:
        PASSED += 1
    else:
        FAILED.append("%s %s" % (label, detail))
    print("%s %s%s" % ("PASS" if cond else "FAIL", label, (" | " + str(detail)) if detail else ""))


def code_of(fn):
    try:
        fn()
    except ol.OriginError as exc:
        return exc.code
    except Exception as exc:  # noqa: BLE001 — an unwrapped crash is itself the failure
        return "UNWRAPPED:" + type(exc).__name__
    return "no-raise"


def fresh_dir(rel=""):
    path = os.path.join(BASE, rel) if rel else BASE
    if os.path.isdir(path):
        for root, dirs, files in os.walk(path):
            for name in files + dirs:
                try:
                    os.chmod(os.path.join(root, name), stat.S_IWRITE)
                except OSError:
                    pass
        shutil.rmtree(path, ignore_errors=True)
    os.makedirs(path, exist_ok=True)
    return path


def main():
    fresh_dir()

    # ---------------------------------------------------------------- naming
    ok("blank path means default", ol._norm_target("   ", "png") == "")
    ok("dot is a directory", code_of(lambda: ol._norm_target(".", "png")) == "bad_path")
    ok("bare repo dir rejected",
       code_of(lambda: ol._norm_target(HERE, "png")) == "bad_path")
    ok("dotfile name rejected",
       code_of(lambda: ol._norm_target(os.path.join(BASE, "  .png  "), "png")) == "bad_path")
    ok("drive root rejected", code_of(lambda: ol._norm_target("C:\\", "png")) == "bad_path")
    ok("trailing space stripped from component",
       os.path.basename(os.path.dirname(
           ol._norm_target(os.path.join(BASE, "trailsp ", "y.png")))) == "trailsp")
    ok("trailing dot stripped from file",
       ol._norm_target(os.path.join(BASE, "a.png.")) == os.path.join(BASE, "a.png"))
    ok("fmt wins over the extension",
       ol._norm_target(os.path.join(BASE, "x.tiff"), "tif") == os.path.join(BASE, "x.tif"))
    ok("no fmt keeps the name",
       ol._norm_target(os.path.join(BASE, "x.opju")) == os.path.join(BASE, "x.opju"))
    ok("chinese preserved",
       ol._norm_target(os.path.join(BASE, "中文测试", "图谱.png"))
       == os.path.join(BASE, "中文测试", "图谱.png"))
    ok("tilde expanded", os.path.expanduser("~") in ol._norm_target("~/a.png"))

    # ------------------------------------------------------------- writability
    ok("unmapped drive is bad_path",
       code_of(lambda: ol._prepare_write(ol._norm_target(r"Q:\nope\x.png"))) == "bad_path")
    ok("dead UNC host is bad_path",
       code_of(lambda: ol._prepare_write(ol._norm_target(r"\\nope-not-real\x.png"))) == "bad_path")
    adir = fresh_dir("adir")
    ok("directory target is bad_path", code_of(lambda: ol._prepare_write(adir)) == "bad_path")
    ro = os.path.join(fresh_dir("ro"), "locked.png")
    with open(ro, "wb") as fh:
        fh.write(b"marker")
    os.chmod(ro, 0o444)
    ok("read-only target is target_locked",
       code_of(lambda: ol._prepare_write(ro)) == "target_locked")
    ok("read-only file untouched", open(ro, "rb").read() == b"marker")
    os.chmod(ro, 0o666)
    ok("writable existing passes", code_of(lambda: ol._prepare_write(ro)) == "no-raise")
    deep = os.path.join(BASE, "made", "by", "us")
    ol._prepare_write(os.path.join(deep, "x.png"))
    ok("missing dirs get created", os.path.isdir(deep))

    # -------------------------------------------------- Origin's '#' rewriting
    real = os.path.join(BASE, "100%_a1b[1].png")
    want = os.path.join(BASE, "100%_a#b[1].png")
    with open(real, "wb") as fh:
        fh.write(b"PNGDATA")
    got, note = ol._written_or_mangled(want)
    ok("# result renamed back to what was asked", got == want and os.path.isfile(want))
    ok("mangled sibling is gone", not os.path.exists(real))
    ok("warning explains the filter", bool(note) and "滤镜" in note, note)
    two_real = os.path.join(BASE, "graph01final02.png")
    two_want = os.path.join(BASE, "graph##final###.png")
    with open(two_real, "wb") as fh:
        fh.write(b"PNGDATA")
    got2, _n2 = ol._written_or_mangled(two_want)
    ok("multi-digit # runs handled", got2 == two_want and os.path.isfile(two_want))
    amb = fresh_dir("amb")
    for name in ("g1.png", "g2.png"):
        with open(os.path.join(amb, name), "wb") as fh:
            fh.write(b"x")
    got3, _n3 = ol._written_or_mangled(os.path.join(amb, "g#.png"))
    ok("ambiguous match is not guessed", got3 is None)
    ok("plain missing file stays a failure",
       ol._written_or_mangled(os.path.join(BASE, "never.png")) == (None, None))

    # ------------------------------------------------------------ CWD litter
    before = set(os.listdir(HERE))
    ok("relative path warns", bool(ol._rel_note("out.png")))
    ok("absolute path silent", ol._rel_note(os.path.join(BASE, "a.png")) is None)
    ok("blank path silent", ol._rel_note("   ") is None)
    ok("no file written into the package dir by these checks",
       set(os.listdir(HERE)) == before, sorted(set(os.listdir(HERE)) - before))

    print("\n%d/%d path checks passed" % (PASSED, PASSED + len(FAILED)))
    if FAILED:
        for f in FAILED:
            print("  FAILED:", f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
