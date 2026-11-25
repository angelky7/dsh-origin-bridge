"""Checks that "it worked" is backed by bytes, never by a return value.

Every write tool in this bridge is supposed to read back evidence, and the one
thing existence cannot prove is change: when a target file already exists
(re-exporting the same figure, saving onto a name used earlier), `os.path.isfile`
is true *before* Origin does anything. Measured with a stubbed `save_fig`/`op.save`
that returned True and wrote nothing: export() answered ok with the previous run's
bytes and dimensions, export_all() answered proof_level "verified", save_project()
answered {"saved": true}, and a year-old `g1.png` was even renamed over the
requested `g#.png` and reported as "the # filter rewrote your name".

Everything here runs without Origin: COM entry points are replaced by fakes, so
what is asserted is the decision logic, not Origin's behaviour.
"""

import json
import os
import shutil
import struct
import sys
import tempfile
import time
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import originlab  # noqa: E402

fails = []


def check(label, cond, got=""):
    if cond:
        print("  ok   %s" % label)
    else:
        print("  FAIL %s  -> %r" % (label, got))
        fails.append(label)


class Restore(object):
    """Patch module attributes for one test and always put them back."""

    def __init__(self, module, **attrs):
        self.module = module
        self.new = attrs
        self.old = {}

    def __enter__(self):
        for key, value in self.new.items():
            self.old[key] = getattr(self.module, key)
            setattr(self.module, key, value)
        return self

    def __exit__(self, *_exc):
        for key, value in self.old.items():
            setattr(self.module, key, value)
        return False


def inline_call(fn, *args, **_options):
    """Stand-in for originlab.call: run the body here instead of on the COM thread.

    call()'s keyword options (timeout / retry_safe / cancel) describe the worker,
    not the function, so they are dropped.
    """
    return fn(*args)


def fake_png(path, width=1600, height=900, size=2000):
    """A file that passes _verify_file: PNG magic, an IHDR with real dimensions."""
    head = (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\r" + b"IHDR"
            + struct.pack(">II", width, height))
    with open(path, "wb") as fh:
        fh.write(head + b"\x00" * max(0, size - len(head)))
    return path


class GPage(object):
    """A graph page: the class name matters, close_pages filters on it."""

    def __init__(self, name, hook=None, sink=None):
        self.name = name
        self.lname = name
        self._hook = hook
        self._sink = sink

    def save_fig(self, path, fmt=None, width=None):
        return self._hook(path, fmt, width) if self._hook else True

    def destroy(self):
        if self._sink is not None:
            self._sink.remove(self)


def fake_op(folder, pages=()):
    return types.SimpleNamespace(
        path=lambda _token=None: folder,
        pages=lambda *_a: list(pages),
        save=lambda *_a, **_k: True,
        org_ver="10.1",
    )


# --------------------------------------------------------------- path evidence


def test_stamp_logic(tmp):
    one = fake_png(os.path.join(tmp, "a.png"))
    stamp = originlab._stamp(one)
    check("a stamp is (mtime_ns, size)", isinstance(stamp, tuple) and len(stamp) == 2, stamp)
    check("an untouched file is not proof of a write",
          originlab._written_or_mangled(one, since=stamp) == (None, None),
          originlab._written_or_mangled(one, since=stamp))
    # Rewrite with a different length: the size moves even if mtime resolution is coarse.
    fake_png(one, size=2300)
    check("a file rewritten after the stamp is proof",
          (originlab._written_or_mangled(one, since=stamp) or (None,))[0] == one)
    # Old callers pass no stamp; that path must keep working.
    check("no stamp keeps the legacy behaviour",
          (originlab._written_or_mangled(one, since=None) or (None,))[0] == one)


def test_mangled_candidates(tmp):
    old = fake_png(os.path.join(tmp, "g1.png"))
    # Age the leftover: a candidate from a previous run is exactly this.
    year_ago = time.time() - 365 * 24 * 3600
    os.utime(old, (year_ago, year_ago))
    old_bytes = open(old, "rb").read()
    requested = os.path.join(tmp, "g#.png")
    started = None          # the requested name never existed before this call
    t0 = time.time_ns()
    got = originlab._written_or_mangled(requested, since=started, not_before=t0)
    check("a stale #-collapsed candidate is not claimed", got == (None, None), got)
    check("a stale candidate keeps its own name", os.path.isfile(old))
    check("a stale candidate keeps its bytes", open(old, "rb").read() == old_bytes)

    t0 = time.time_ns()
    fresh = fake_png(os.path.join(tmp, "h1.png"), size=2300)
    want = os.path.join(tmp, "h#.png")
    got = originlab._written_or_mangled(want, since=originlab._stamp(want), not_before=t0)
    check("a fresh #-collapsed candidate is renamed back", got[0] == want, got)
    check("the rename really happened", os.path.isfile(want) and not os.path.isfile(fresh))
    check("the rename explains itself", bool(got[1]) and "#" in got[1], got[1:])


# ------------------------------------------------------------------- export()


def test_export(tmp):
    target = fake_png(os.path.join(tmp, "re.png"), width=800, height=600)
    before = open(target, "rb").read()

    idle = GPage("Graph1", hook=lambda path, fmt=None, width=None: True)
    with Restore(originlab, call=inline_call,
                 origin=lambda: fake_op(tmp), _graph_of=lambda _op, _ref: idle):
        try:
            originlab.export("", path=target, fmt="png", width=1600)
            err = None
        except originlab.OriginError as exc:
            err = exc
    check("re-export onto an unchanged file is refused",
          err is not None and err.code == "export_unverified", err and err.code)
    check("the old bytes were not passed off as new", open(target, "rb").read() == before)

    writer = GPage("Graph1", hook=lambda path, fmt=None, width=None: fake_png(path, 1600, 900, 2400))
    with Restore(originlab, call=inline_call,
                 origin=lambda: fake_op(tmp), _graph_of=lambda _op, _ref: writer):
        try:
            out = originlab.export("", path=target, fmt="png", width=1600)
        except originlab.OriginError as exc:
            out = {"ok": False, "code": exc.code}
    check("a real export still passes", out.get("ok") is True, out)
    check("the exported file is the new one", out.get("bytes") == 2400, out.get("bytes"))


def test_export_all(tmp):
    stale = fake_png(os.path.join(tmp, "Graph1.png"))
    idle = GPage("Graph1", hook=lambda path, fmt=None, width=None: True)
    with Restore(originlab, call=inline_call,
                 origin=lambda: fake_op(tmp, [idle]), _graph_of=lambda _op, _ref: idle):
        out = originlab.export_all(tmp, fmt="png")
    codes = [f["code"] for f in out.get("failed") or []]
    check("a page whose file did not move lands in failed", codes == ["export_unverified"], codes)
    check("and the batch is not called verified",
          out.get("proof_level") == "unverified", out.get("proof_level"))
    check("the old page file is left as it was", os.path.getsize(stale) == 2000)


# -------------------------------------------------------------- save_project()


def test_save_project(tmp):
    target = os.path.join(tmp, "old.opju")
    with open(target, "wb") as fh:
        fh.write(b"\x00" * 1100)
    before = open(target, "rb").read()
    op = fake_op(tmp)
    with Restore(originlab, call=inline_call, origin=lambda: op, _project_file=""):
        try:
            originlab.save_project(target)
            err = None
        except originlab.OriginError as exc:
            err = exc
    check("saving onto an unchanged project is refused",
          err is not None and err.code == "save_unverified", err and err.code)
    check("the previous project file is untouched", open(target, "rb").read() == before)

    def real_save(path=None):
        with open(path, "wb") as fh:
            fh.write(b"\x00" * 900)
        return True

    op2 = fake_op(tmp)
    op2.save = real_save
    fresh = os.path.join(tmp, "new.opju")
    with Restore(originlab, call=inline_call, origin=lambda: op2, _project_file=""):
        out = originlab.save_project(fresh)
    check("a real save still reports saved", out.get("saved") is True, out.get("saved"))


# ---------------------------------------------------------------- close_pages()


def test_close_pages(tmp):
    pages = []
    op = types.SimpleNamespace(pages=lambda *_a: list(pages))
    with Restore(originlab, call=inline_call, origin=lambda: op):
        a, b = GPage("Graph1", sink=pages), GPage("Graph2", sink=pages)
        pages.extend([a, b])
        out = originlab.close_pages(kind="graph", names=[])
        check("names=[] closes nothing", out.get("closed") == [], out)
        check("names=[] leaves both pages alive", len(pages) == 2, len(pages))
        check("names=[] says what it means", "空列表" in (out.get("note") or ""), out.get("note"))

        out = originlab.close_pages(kind="graph")
        check("omitting names still closes the whole kind",
              sorted(out.get("closed") or []) == ["Graph1", "Graph2"], out.get("closed"))
        check("the pages really are gone", pages == [], pages)


# ------------------------------------------------------------------ ownership


def test_ownership(tmp):
    user_row = {"pid": 4321, "started": "2026-11-01T09:00:00", "window": 1}

    def connect(probe_sequence):
        seq = list(probe_sequence)

        def probe():
            return seq.pop(0) if seq else [user_row]
        sys.modules["originpro"] = types.SimpleNamespace(path=lambda: None)
        try:
            with Restore(originlab, _process_rows=probe,
                         _session_lock_acquire=lambda: None,
                         _SESSION_DIR=tmp, _started_pids=set(),
                         _op=None, _ownership_uncertain=False,
                         _busy_dialogs=lambda pid: []):
                originlab.origin()
                return (set(originlab._started_pids), originlab._owned_load(),
                        originlab._row_owned(user_row), originlab._ownership_uncertain,
                        originlab.instances())
        finally:
            sys.modules.pop("originpro", None)

    started, owned, mine, uncertain, table = connect([None, [user_row]])
    check("a failed pre-connect probe claims nothing", started == set(), started)
    check("a failed probe writes no ownership record", owned == {}, owned)
    check("the user's own Origin is not treated as ours", mine is False, mine)
    check("the uncertainty is reported, not hidden", uncertain is True
          and table.get("ownership") == "uncertain", (uncertain, table.get("ownership")))

    started, owned, mine, uncertain, _table = connect([[], [user_row]])
    check("a new pid IS claimed when the probe worked", started == {4321}, started)
    check("and recorded on disk for the next session", owned.get(4321) == user_row["started"], owned)
    check("so that instance counts as ours", mine is True, mine)
    check("no false uncertainty is reported", uncertain is False, uncertain)


# ------------------------------------------------------------- sanitized numpy


def test_numpy_scalars(tmp):
    try:
        import numpy as np
    except ImportError:
        print("  SKIP numpy not installed; numpy scalar checks skipped")
        return
    check("np.float32 inf -> null", originlab.sanitize(np.float32("inf")) is None)
    check("np.float16 nan -> null", originlab.sanitize(np.float16("nan")) is None)
    got = originlab.sanitize(np.int64(7))
    check("np.int64 -> plain int", got == 7 and isinstance(got, int), got)
    got = originlab.sanitize(np.bool_(True))
    check("np.bool_ -> plain bool", got is True, got)
    got = originlab.sanitize(np.array([1.0, float("inf"), 3.0]))
    check("ndarray -> list with non-finite folded to null", got == [1.0, None, 3.0], got)
    try:
        json.dumps(originlab.sanitize({"v": [np.float32("nan"), np.int64(2)]}),
                   allow_nan=False)
        clean = True
    except (TypeError, ValueError) as exc:
        clean = exc
    check("a sanitized numpy payload is strict JSON", clean is True, clean)


def test_status_is_sanitized(tmp):
    op = types.SimpleNamespace(path=lambda _t=None: "", org_ver=float("inf"),
                               pages=lambda *_a: [])
    with Restore(originlab, call=inline_call, origin=lambda: op):
        out = originlab.status()
    try:
        json.dumps(out, allow_nan=False)
        strict = True
    except (TypeError, ValueError) as exc:
        strict = exc
    check("status() output is strict JSON", strict is True, strict)
    check("status() folds a non-finite version to null",
          out.get("origin_version_raw") is None, out.get("origin_version_raw"))


def main():
    tmp = tempfile.mkdtemp(prefix="origin_bridge_evidence_")
    try:
        test_stamp_logic(tmp)
        test_mangled_candidates(tmp)
        test_export(tmp)
        test_export_all(tmp)
        test_save_project(tmp)
        test_close_pages(tmp)
        test_ownership(tmp)
        test_numpy_scalars(tmp)
        test_status_is_sanitized(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if fails:
        print("\n%d FAILED: %s" % (len(fails), ", ".join(fails)))
        return 1
    print("\nall evidence checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())