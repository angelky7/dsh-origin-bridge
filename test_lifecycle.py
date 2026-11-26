"""Checks for the Origin instance-lifecycle layer: classification and reclaim.

These run without Origin: the process table and taskkill are stubbed, so the
safety property that matters -- a windowed instance is never killed -- is
asserted against a synthetic set of instances.
"""

import json as _json
import os
import shutil
import subprocess as _sp
import sys

# 控制台代码页不是我们能选的：中文 Windows 默认 cp936、西文 cp1252，而检查标签里
# 有 µ、°、≤ 和中文。一条检查都还没跑就 UnicodeEncodeError，等于把"你可以自己复跑"
# 这条路堵死——所以先把两个输出流设成"写不出来的字符用 ? 代替"，而不是崩。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import originlab  # noqa: E402

# Keep a handle on the real probe: the with_fake helper replaces _process_rows by
# design, and the checks about ITS semantics must call the original.
originlab._real_process_rows = originlab._process_rows

fails = []


def check(label, cond, got=""):
    if cond:
        print("  ok   %s" % label)
    else:
        print("  FAIL %s  -> %r" % (label, got))
        fails.append(label)


class Fake(object):
    """Stands in for the process table and taskkill."""

    def __init__(self, rows):
        self.rows = rows
        self.killed = []
        self.forced = []

    def process_rows(self):
        return [dict(r) for r in self.rows]

    def terminate(self, pid, force=False):
        (self.forced if force else self.killed).append(pid)
        if force:
            self.rows = [r for r in self.rows if r["pid"] != pid]
        return None

    def sleep(self, _seconds):
        return None


def with_fake(rows, fn):
    fake = Fake(rows)
    tmp = tempfile.mkdtemp(prefix="origin_bridge_owned_")
    saved = (originlab._process_rows, originlab._terminate, originlab._close_pid,
             originlab.time.sleep, set(originlab._started_pids),
             originlab._SESSION_DIR, originlab._SESSION_LOCK)
    originlab._process_rows = fake.process_rows
    originlab._terminate = fake.terminate
    originlab._close_pid = lambda pid, started="", verify_timeout=5.0: (
        fake.terminate(pid), fake.terminate(pid, force=True), "force_closed")[2]
    originlab.time.sleep = fake.sleep
    originlab._SESSION_DIR = tmp
    originlab._SESSION_LOCK = os.path.join(tmp, "session.lock")
    try:
        return fn(fake)
    finally:
        originlab._process_rows, originlab._terminate, originlab._close_pid = saved[0], saved[1], saved[2]
        originlab.time.sleep = saved[3]
        originlab._started_pids.clear()
        originlab._started_pids.update(saved[4])
        originlab._SESSION_DIR = saved[5]
        originlab._SESSION_LOCK = saved[6]
        shutil.rmtree(tmp, ignore_errors=True)


BACK = {"pid": 1001, "started": "2026-10-10T12:00:00", "window": 0}
BACK2 = {"pid": 1002, "started": "2026-10-10T12:05:00", "window": 0}
FRONT = {"pid": 2001, "started": "2026-10-10T11:00:00", "window": 131072}

print("instances() 分类")
rows = [dict(BACK), dict(BACK2), dict(FRONT)]


def _classify(_fake):
    originlab._started_pids.add(1002)
    info = originlab.instances()
    check("后台两个", [r["pid"] for r in info["background"]] == [1001, 1002], info["background"])
    check("前台一个", [r["pid"] for r in info["foreground"]] == [2001], info["foreground"])
    check("count 对", info["count"] == 3, info["count"])
    back_by_pid = {r["pid"]: r for r in info["background"]}
    check("标出我们起的", back_by_pid[1002]["ours"] and not back_by_pid[1001]["ours"],
          {p: b["ours"] for p, b in back_by_pid.items()})
    check("前台窗口标记为 foreground", info["foreground"][0]["foreground"] is True)


with_fake(rows, _classify)

print("reclaim() 默认只动本桥启动的实例")


def _reclaim_default(_fake):
    out = originlab.reclaim()
    check("归属不明的后台一个都没动", _fake.forced == [] and _fake.killed == [],
          (_fake.killed, _fake.forced))
    check("unowned 里列出两个", sorted(u["pid"] for u in out["unowned"]) == [1001, 1002],
          out["unowned"])
    check("通知给出 include_orphans 出口", "include_orphans" in out["notify"], out["notify"])
    check("没碰前台 2001", 2001 not in _fake.killed and 2001 not in _fake.forced,
          (_fake.killed, _fake.forced))
    check("left_running 里是前台", out["left_running"] == [2001], out["left_running"])


with_fake([dict(BACK), dict(BACK2), dict(FRONT)], _reclaim_default)


def _reclaim_owned(_fake):
    originlab._started_pids.add(1002)
    out = originlab.reclaim()
    check("只关本会话启动的 1002", _fake.forced == [1002], _fake.forced)
    check("1001 仍在 unowned", [u["pid"] for u in out["unowned"]] == [1001], out["unowned"])
    check("force_closed 记录 1002", out["force_closed"] == [1002], out)
    check("通知文案提到窗口", "有窗口" in out["notify"], out["notify"])


with_fake([dict(BACK), dict(BACK2), dict(FRONT)], _reclaim_owned)


def _reclaim_orphans_force(_fake):
    out = originlab.reclaim(include_orphans=True)
    check("显式 include_orphans 才关两个", sorted(_fake.forced) == [1001, 1002], _fake.forced)
    check("forced 后 unowned 为空", out["unowned"] == [], out["unowned"])


with_fake([dict(BACK), dict(BACK2), dict(FRONT)], _reclaim_orphans_force)


def _reclaim_dry_run(_fake):
    originlab._started_pids.add(1002)
    out = originlab.reclaim(close_background=False)
    check("预览模式没杀任何进程", not _fake.killed and not _fake.forced, (_fake.killed, _fake.forced))
    check("预览模式只列出自己的", out["skipped"] == [1002], out.get("skipped"))
    check("预览也列出归属不明的", [u["pid"] for u in out["unowned"]] == [1001], out["unowned"])


with_fake([dict(BACK), dict(BACK2), dict(FRONT)], _reclaim_dry_run)


def _reclaim_registry(_fake):
    # 上次运行留下的登记：pid + 启动时间都对上才算自有
    with open(originlab._owned_file(), "w", encoding="utf-8") as fh:
        _json.dump({"1001": BACK["started"], "1002": "2020-01-01T00:00:00"}, fh)
    out = originlab.reclaim()
    check("启动时间匹配的旧记录被回收", _fake.forced == [1001], _fake.forced)
    check("启动时间不匹配（pid 复用）不关", 1002 not in _fake.forced, out["unowned"])
    with open(originlab._owned_file(), encoding="utf-8") as fh:
        reg = _json.load(fh)
    check("回收后登记里删掉 1001", "1001" not in reg, reg)


with_fake([dict(BACK), dict(BACK2), dict(FRONT)], _reclaim_registry)


def _reclaim_safe_worker(_fake):
    originlab._started_pids.add(1002)
    originlab._thread_trace["reset"].clear()
    originlab.reclaim_safe()
    tids = list(originlab._thread_trace["reset"])
    check("reclaim_safe 把断连放到 COM worker 上",
          bool(tids) and originlab._thread is not None
          and tids[-1] == originlab._thread.ident, tids)
    check("reclaim_safe 仍然关掉自有实例", _fake.forced == [1002], _fake.forced)


with_fake([dict(BACK), dict(BACK2), dict(FRONT)], _reclaim_safe_worker)


def _reclaim_all_background(_fake):
    originlab._started_pids.add(1001)
    out = originlab.reclaim()
    check("全是后台时通知说没有前台", "没有发现前台窗口" in out["notify"], out["notify"])
    check("left_running 为空", out["left_running"] == [], out["left_running"])


with_fake([dict(BACK)], _reclaim_all_background)


def _reclaim_refuses_when_other_session_holds(_fake):
    child = _sp.Popen([sys.executable, "-c", "import time; time.sleep(20)"])
    try:
        with open(originlab._SESSION_LOCK, "w", encoding="utf-8") as fh:
            _json.dump({"bridge_pid": child.pid, "started_at": "2026-01-01T00:00:00"}, fh)
        out = originlab.reclaim()
        check("别的实时会话持锁时回收拒绝动手",
              out.get("probe") == "locked" and not _fake.forced and not _fake.killed, out)
        check("拒绝时给出持有者 pid", out.get("locked_by") == child.pid, out.get("locked_by"))
    finally:
        child.terminate()
        child.wait(timeout=10)


with_fake([dict(BACK)], _reclaim_refuses_when_other_session_holds)

print("_release_on_exit() 只回收自己起的，且先清理后解锁")


def _atexit_scope(_fake):
    originlab._started_pids.add(1002)
    killed = []
    originlab._close_pid = lambda pid, started="", verify_timeout=5.0: (
        killed.append(pid) or "closed")
    originlab._release_on_exit()
    check("只关了 1002", killed == [1002], killed)
    check("1001 是别人的孤儿，没在退出时动它", 1001 not in killed, killed)


with_fake([dict(BACK), dict(BACK2), dict(FRONT)], _atexit_scope)


def _atexit_noop(_fake):
    originlab._started_pids.clear()
    killed = []
    originlab._close_pid = lambda pid, started="", verify_timeout=5.0: killed.append(pid) or "closed"
    originlab._release_on_exit()
    check("自己没起实例时什么都不做", killed == [], killed)


with_fake([dict(BACK), dict(FRONT)], _atexit_noop)


def _atexit_ordering(_fake):
    originlab._started_pids.add(1002)
    originlab._session_lock_acquire()
    seen = []

    def close(pid, started="", verify_timeout=5.0):
        seen.append(os.path.exists(originlab._SESSION_LOCK))
        return "closed"

    originlab._close_pid = close
    originlab._release_on_exit()
    check("清理过程中锁还在（先清理后解锁）", seen == [True], seen)
    check("清理完成后锁才消失", not os.path.exists(originlab._SESSION_LOCK))


with_fake([dict(BACK), dict(BACK2), dict(FRONT)], _atexit_ordering)

print("探测失败时不能谎报干净")


def _probe_unavailable(_fake):
    originlab._process_rows = lambda: None          # powershell 不可用 / 超时
    killed = []
    originlab._close_pid = lambda pid, started="", verify_timeout=5.0: killed.append(pid) or "closed"

    info = originlab.instances()
    check("instances 报告探测失败", info.get("probe") == "unavailable", info)
    check("instances 不假装数量为 0", info.get("count") is None, info.get("count"))
    check("instances 给了下一步建议", bool(info.get("next_actions")), info.get("next_actions"))

    out = originlab.reclaim()
    check("reclaim 什么都没杀", not killed, killed)
    check("reclaim 标出探测失败", out.get("probe") == "unavailable", out)
    check("reclaim 不说『已回收全部』", "没关" in out.get("notify", "")
          or "探测失败" in out.get("notify", ""), out.get("notify"))

    originlab._started_pids.add(1002)
    originlab._release_on_exit()
    check("退出钩子在探测失败时不动手", killed == [], killed)


with_fake([dict(BACK), dict(FRONT)], _probe_unavailable)

print("探测不可用 / 没关掉 / 清理抛异常时，锁不能放（fail-closed）")


def _lock_kept(_fake, label, close_verdict=None, close_raises=False, rows_mode="ok"):
    """The lock may only go when the instance is PROVEN gone."""
    originlab._started_pids.clear()
    originlab._started_pids.add(1002)
    originlab._session_lock_acquire()

    if rows_mode == "none":
        originlab._process_rows = lambda: None
    elif rows_mode == "empty":
        originlab._process_rows = lambda: []
    else:
        originlab._process_rows = lambda: [dict(BACK2)]

    if close_raises:
        def boom(pid, started="", verify_timeout=5.0):
            raise OSError("taskkill exploded")
        originlab._close_pid = boom
    else:
        originlab._close_pid = lambda pid, started="", verify_timeout=5.0: close_verdict

    originlab._release_on_exit()
    kept = os.path.exists(originlab._SESSION_LOCK)
    check("%s -> 锁保留" % label, kept, kept)
    if kept:
        originlab._session_lock_release()


with_fake([dict(BACK2)], lambda f: _lock_kept(f, "探测不可用", rows_mode="none"))
with_fake([dict(BACK2)], lambda f: _lock_kept(f, "关闭结果 stuck", close_verdict="stuck"))
with_fake([dict(BACK2)], lambda f: _lock_kept(f, "关闭结果 unverified", close_verdict="unverified"))
with_fake([dict(BACK2)], lambda f: _lock_kept(f, "清理抛异常", close_raises=True))


def _lock_released_when_probe_says_gone(_fake):
    # An EMPTY table is different from an unavailable one: _process_rows only ever
    # answers [] when it really looked and found nothing, so our instance is gone and
    # the lock may go.
    originlab._started_pids.clear()
    originlab._started_pids.add(1002)
    originlab._session_lock_acquire()
    originlab._process_rows = lambda: []
    originlab._release_on_exit()
    check("探测说实例都没了 -> 放锁", not os.path.exists(originlab._SESSION_LOCK))


with_fake([], _lock_released_when_probe_says_gone)


def _lock_released(_fake):
    originlab._started_pids.clear()
    originlab._started_pids.add(1002)
    originlab._session_lock_acquire()
    originlab._process_rows = lambda: [dict(BACK2)]
    originlab._close_pid = lambda pid, started="", verify_timeout=5.0: "closed"
    originlab._release_on_exit()
    check("确认关掉后锁才放", not os.path.exists(originlab._SESSION_LOCK))


with_fake([dict(BACK2)], _lock_released)


def _lock_released_when_instance_already_gone(_fake):
    originlab._started_pids.clear()
    originlab._started_pids.add(1002)
    originlab._session_lock_acquire()
    # The instance vanished before we looked: it is gone, so the lock may go.
    originlab._process_rows = lambda: []
    originlab._release_on_exit()
    check("实例本来就没了也放锁", not os.path.exists(originlab._SESSION_LOCK))


with_fake([], _lock_released_when_instance_already_gone)

print("进程探测：powershell 跑了但失败，不许当成『机器上没有 Origin』")


def _probe_semantics(_fake):
    real_run = originlab.subprocess.run
    # with_fake replaced _process_rows with its own stub; this block is about the REAL
    # one, so put it back for the duration.
    real_probe = originlab._real_process_rows

    class Done(object):
        def __init__(self, rc, out, err=""):
            self.returncode = rc
            self.stdout = out
            self.stderr = err

    probe_ok = "__OB_PROBE_OK__"
    cases = [
        ("退出码 1、无输出", Done(1, ""), None),
        ("退出码 0 但没有结束标记", Done(0, "garbage\nalso garbage\n"), None),
        ("退出码 0、正常一行", Done(0, "4321|2026-01-01T00:00:00|0\n" + probe_ok + "\n"),
         [{"pid": 4321, "started": "2026-01-01T00:00:00", "window": 0}]),
        ("退出码 0、真空输出", Done(0, probe_ok + "\n"), []),
        ("退出码 0、有一行解析不了", Done(0, "garbage\n4321|T|0\n" + probe_ok + "\n"), None),
    ]
    originlab._process_rows = real_probe
    for label, done, want in cases:
        originlab.subprocess.run = lambda *_a, done=done, **_k: done
        try:
            got = originlab._process_rows()
        finally:
            originlab.subprocess.run = real_run
        if want is None:
            check("%s -> None" % label, got is None, got)
        else:
            check("%s -> %r" % (label, want), got == want, got)
    # and the failure reason is kept for the tool that gave up
    originlab.subprocess.run = lambda *_a, **_k: Done(1, "", "boom")
    try:
        originlab._process_rows()
        check("探测失败留下了原因", bool(originlab._probe_notes), originlab._probe_notes)
    finally:
        originlab.subprocess.run = real_run


with_fake([], _probe_semantics)

print("_pid_alive：打不开不等于已死")


def _pid_alive_semantics(_fake):
    import ctypes

    class FakeK32(object):
        def __init__(self, handle, err, exit_ok=True, exit_code=259):
            self.handle, self.err = handle, err
            self.exit_ok, self.exit_code = exit_ok, exit_code
            self.last = 0

        def OpenProcess(self, *_a):
            self.last = self.err
            return self.handle

        def GetLastError(self):
            return self.last

        def GetExitCodeProcess(self, _h, ref):
            if not self.exit_ok:
                return 0
            ref._obj.value = self.exit_code
            return 1

        def CloseHandle(self, _h):
            return 1

    real = ctypes.windll.kernel32
    cases = [
        ("ERROR_INVALID_PARAMETER(87) = 不存在", FakeK32(0, 87), False),
        ("ERROR_ACCESS_DENIED(5) = 看不见但活着", FakeK32(0, 5), True),
        ("handle 打不开且原因未知", FakeK32(0, 1234), True),
        ("句柄拿到了、退出码读不出", FakeK32(1, 0, exit_ok=False), True),
        ("句柄拿到了、仍在运行", FakeK32(1, 0, exit_code=259), True),
        ("句柄拿到了、已退出", FakeK32(1, 0, exit_code=0), False),
    ]
    for label, fake, want in cases:
        ctypes.windll.kernel32 = fake
        try:
            got = originlab._pid_alive(424242)
        finally:
            ctypes.windll.kernel32 = real
        check("_pid_alive: %s -> %s" % (label, want), got is want, got)


if os.name == "nt":
    with_fake([], _pid_alive_semantics)

print("_exit_confirmed：我们自己启动的实例还活着时不能说 gone")


def _exit_confirmed_semantics(_fake):
    originlab._started_pids.clear()
    originlab._started_pids.update({5001, 5002})
    before = [{"pid": 5001, "started": "T", "window": 0},
              {"pid": 5002, "started": "T", "window": 0},
              {"pid": 6001, "started": "T", "window": 1}]
    originlab._process_rows = lambda: [{"pid": 5001, "started": "T", "window": 0},
                                       {"pid": 6001, "started": "T", "window": 1}]
    check("我们自己那个还活着 -> alive", originlab._exit_confirmed(None, before) == "alive")
    originlab._process_rows = lambda: [{"pid": 6001, "started": "T", "window": 1}]
    check("我们自己的都没了 -> gone", originlab._exit_confirmed(None, before) == "gone")
    originlab._process_rows = lambda: None
    check("探测不可用 -> unknown", originlab._exit_confirmed(None, before) == "unknown")
    originlab._started_pids.clear()
    originlab._process_rows = lambda: [{"pid": 6001, "started": "T", "window": 1}]
    check("没启动过任何实例时按总数判断", originlab._exit_confirmed(None, before) == "gone")


with_fake([], _exit_confirmed_semantics)

print("_session_lock_release 把失败讲出来")


def _release_reports(_fake):
    originlab._session_lock_acquire()
    ok_released, why = originlab._session_lock_release()
    check("正常释放返回 (True, '')", ok_released is True and why == "", (ok_released, why))

    with open(originlab._SESSION_LOCK, "w", encoding="utf-8") as fh:
        fh.write("{half-written")
    ok_released, why = originlab._session_lock_release()
    check("坏锁不删、但说清楚", ok_released is False and "读不出来" in why, (ok_released, why))
    check("坏锁保持原样", os.path.isfile(originlab._SESSION_LOCK))
    originlab._SESSION_LOCK and os.path.exists(originlab._SESSION_LOCK) and os.remove(originlab._SESSION_LOCK)

    with open(originlab._SESSION_LOCK, "w", encoding="utf-8") as fh:
        _json.dump({"bridge_pid": 999999999, "started_at": "T"}, fh)
    ok_released, why = originlab._session_lock_release()
    check("别人的锁不删、但说清楚",
          ok_released is False and "不是本进程" in why, (ok_released, why))


with_fake([], _release_reports)

print("_close_pid 强杀后核实死亡")


def _close_case(final_rows, label, expected, pid=None):
    pid = os.getpid() if pid is None else pid
    state = {"rows": [{"pid": pid, "started": "T0", "window": 0}]}
    logs = []

    def rows():
        return [dict(r) for r in state["rows"]]

    def term(_pid, force=False):
        logs.append("force" if force else "term")
        if force:
            state["rows"] = [dict(r) for r in final_rows]

    saved = (originlab._process_rows, originlab._terminate, originlab._dismiss_busy_dialogs,
             originlab.time.sleep)
    originlab._process_rows = rows
    originlab._terminate = term
    originlab._dismiss_busy_dialogs = lambda _pid: []
    originlab.time.sleep = lambda _s: None
    try:
        got = originlab._close_pid(pid, "T0", verify_timeout=0)
    finally:
        (originlab._process_rows, originlab._terminate,
         originlab._dismiss_busy_dialogs, originlab.time.sleep) = saved
    check(label, got == expected, (got, logs))


_close_case([], "force 后进程消失 -> force_closed", "force_closed")
_close_case([{"pid": os.getpid(), "started": "T0", "window": 0}],
            "force 后仍在 -> stuck", "stuck")


def _close_reuse_before_force():
    pid = 5000
    calls = {"n": 0}
    logs = []

    def rows():
        calls["n"] += 1
        return [{"pid": pid, "started": "T1" if calls["n"] > 1 else "T0", "window": 0}]

    def term(_pid, force=False):
        logs.append(force)

    saved = (originlab._process_rows, originlab._terminate, originlab._dismiss_busy_dialogs,
             originlab.time.sleep)
    originlab._process_rows = rows
    originlab._terminate = term
    originlab._dismiss_busy_dialogs = lambda _pid: []
    originlab.time.sleep = lambda _s: None
    try:
        got = originlab._close_pid(pid, "T0", verify_timeout=0)
    finally:
        (originlab._process_rows, originlab._terminate,
         originlab._dismiss_busy_dialogs, originlab.time.sleep) = saved
    check("轮询中发现 pid 被复用 -> closed，且不发强杀",
          got == "closed" and logs == [False], (got, logs))


_close_reuse_before_force()


def _close_unverified():
    pid = os.getpid()
    state = {"rows": [{"pid": pid, "started": "T0", "window": 0}]}
    saved = (originlab._process_rows, originlab._terminate, originlab._dismiss_busy_dialogs,
             originlab.time.sleep)
    originlab._process_rows = lambda: state["rows"]
    originlab._terminate = lambda _pid, force=False: state.__setitem__("rows", None)
    originlab._dismiss_busy_dialogs = lambda _pid: []
    originlab.time.sleep = lambda _s: None
    try:
        # 探测不可用 + 进程看起来还活着（os.getpid()）= 无法核实，不是「已关」
        got = originlab._close_pid(pid, "T0", verify_timeout=0)
    finally:
        (originlab._process_rows, originlab._terminate,
         originlab._dismiss_busy_dialogs, originlab.time.sleep) = saved
    check("探测不可用时 -> unverified（不谎报已关）", got == "unverified", got)


_close_unverified()

print("exit_origin 不再吞异常报成功")


class _FakeOp(object):
    def __init__(self, boom=False):
        self.boom = boom

    def exit(self):
        if self.boom:
            raise RuntimeError("boom")


def _exit_case(wait_state, boom, label, expect_ok, expect_code=None):
    tmp = tempfile.mkdtemp(prefix="origin_bridge_exit_")
    saved = (originlab.origin, originlab._save_project, originlab._process_rows,
             originlab._wait_pid_gone, originlab._dismiss_all_busy_dialogs,
             set(originlab._started_pids),
             originlab._SESSION_DIR, originlab._SESSION_LOCK)
    originlab._SESSION_DIR = tmp
    originlab._SESSION_LOCK = os.path.join(tmp, "session.lock")
    originlab._started_pids.clear()
    originlab._started_pids.add(4242)
    originlab._session_lock_acquire()          # a real session holds the lock
    originlab.origin = lambda: _FakeOp(boom)
    originlab._save_project = lambda p: {"path": ""}
    originlab._process_rows = lambda: [{"pid": 4242, "started": "T0", "window": 0}]
    originlab._wait_pid_gone = lambda pid, started="", timeout=5.0: wait_state
    originlab._dismiss_all_busy_dialogs = lambda: []
    try:
        try:
            out = originlab.exit_origin()
            ok = True
            got = out
        except originlab.OriginError as exc:
            ok = False
            got = exc
        if expect_ok:
            check(label + "：报告 exited", ok and got.get("exited") is True, got)
            check(label + "：确认退出后才解锁",
                  not os.path.exists(originlab._SESSION_LOCK), originlab._SESSION_LOCK)
            if boom:
                check(label + "：保留退出报错作为 warning",
                      "boom" in (got.get("warning") or ""), got)
        else:
            check(label + "：报 %s" % expect_code,
                  (not ok) and got.code == expect_code, got)
            check(label + "：锁不释放（Origin 可能还在跑）",
                  os.path.exists(originlab._SESSION_LOCK), originlab._SESSION_LOCK)
            if boom:
                check(label + "：错误信息带出底层异常", "boom" in got.message, got.message)
    finally:
        (originlab.origin, originlab._save_project, originlab._process_rows,
         originlab._wait_pid_gone, originlab._dismiss_all_busy_dialogs) = (
            saved[0], saved[1], saved[2], saved[3], saved[4])
        originlab._started_pids.clear()
        originlab._started_pids.update(saved[5])
        originlab._SESSION_DIR = saved[6]
        originlab._SESSION_LOCK = saved[7]
        shutil.rmtree(tmp, ignore_errors=True)


_exit_case("gone", False, "进程确认退出", True)
_exit_case("gone", True, "退出报错但进程已退出", True)
_exit_case("alive", False, "进程没退", False, "exit_unconfirmed")
_exit_case("alive", True, "进程没退且 op.exit 报错", False, "exit_unconfirmed")

print("exit_origin 默认不退出别人启动的 Origin")


def _exit_foreign(force, expect_ok):
    tmp = tempfile.mkdtemp(prefix="origin_bridge_foreign_")
    saved = (originlab.origin, originlab._save_project, originlab._process_rows,
             originlab._wait_pid_gone, originlab._dismiss_all_busy_dialogs,
             set(originlab._started_pids),
             originlab._SESSION_DIR, originlab._SESSION_LOCK)
    calls = []
    state = {"exited": False}
    fake = _FakeOp(False)
    real_exit = fake.exit
    fake.exit = lambda: (calls.append("exit"), state.__setitem__("exited", True),
                         real_exit())[2]
    originlab._SESSION_DIR = tmp
    originlab._SESSION_LOCK = os.path.join(tmp, "session.lock")
    originlab._started_pids.clear()             # attached to a pre-existing instance
    originlab._session_lock_acquire()
    originlab.origin = lambda: fake
    originlab._save_project = lambda p: {"path": ""}
    originlab._process_rows = lambda: ([] if state["exited"] else
                                      [{"pid": 7777, "started": "T0", "window": 0}])
    originlab._wait_pid_gone = lambda pid, started="", timeout=5.0: "gone"
    originlab._dismiss_all_busy_dialogs = lambda: []
    try:
        try:
            out = originlab.exit_origin(force=force)
            ok, got = True, out
        except originlab.OriginError as exc:
            ok, got = False, exc
        if expect_ok:
            check("force=true 时退出外部实例", ok and got.get("exited") is True, got)
            check("force=true 确实调了 op.exit", calls == ["exit"], calls)
        else:
            check("外部实例默认拒绝退出", (not ok) and got.code == "foreign_instance", got)
            check("拒绝时不碰 op.exit", calls == [], calls)
            check("拒绝时锁仍由本会话持有",
                  os.path.exists(originlab._SESSION_LOCK), originlab._SESSION_LOCK)
    finally:
        (originlab.origin, originlab._save_project, originlab._process_rows,
         originlab._wait_pid_gone, originlab._dismiss_all_busy_dialogs) = (
            saved[0], saved[1], saved[2], saved[3], saved[4])
        originlab._started_pids.clear()
        originlab._started_pids.update(saved[5])
        originlab._SESSION_DIR = saved[6]
        originlab._SESSION_LOCK = saved[7]
        shutil.rmtree(tmp, ignore_errors=True)


_exit_foreign(False, False)
_exit_foreign(True, True)

print("掉线重连（call 层）")
_real_reset = originlab._reset_connection

check("SystemError 算掉线", originlab._dead_app(
    SystemError("<built-in function ApplicationBase_Save> returned a result with an exception set")))
check("originpro 报的连接失败算掉线", originlab._dead_app(
    originlab.OriginError("connection_error", "连不上 Origin 自动化服务器："
                          "<built-in function ApplicationBase_Path> returned a result"
                          " with an exception set")))
check("参数错误不算掉线", not originlab._dead_app(
    originlab.OriginError("bad_column", "列号从 1 开始，收到 0")))
check("被包成 internal_error 的 SystemError 也算掉线", originlab._dead_app(
    originlab.OriginError("internal_error",
                          "SystemError: <built-in function ApplicationBase_Save> returned a "
                          "result with an exception set")))
check("普通 ValueError 不算掉线", not originlab._dead_app(ValueError("识别不出分隔符")))

tries = []


def _dies_once(*a, **k):
    tries.append(1)
    if len(tries) == 1:
        raise SystemError("<built-in function ApplicationBase_Save> returned a result "
                          "with an exception set")
    return "recovered"


resets = []
originlab._reset_connection = lambda: resets.append(1) or True
try:
    check("声明只读的函数掉线后自动重连",
          originlab.call(_dies_once, retry_safe=True) == "recovered", tries)
    check("只重试一次", len(tries) == 2 and len(resets) == 1, (tries, resets))

    # P0-2 回归：写操作不允许自动重放，但坏指针仍要丢掉（否则后续调用全撞同一个死连接）。
    tries[:] = []
    resets[:] = []
    try:
        originlab.call(_dies_once)               # 默认 retry_safe=False
        check("写操作默认不重放", False, "本该抛错")
    except SystemError as exc:
        check("写操作默认不重放（原始错误直接交出）",
              "ApplicationBase_Save" in str(exc) and tries == [1] and resets == [1],
              (tries, resets, str(exc)))

    tries[:] = []
    resety = []
    originlab._reset_connection = lambda: resety.append(1) or False
    try:
        originlab.call(_dies_once, retry_safe=True)
        check("重连不可用时不无限重试", False, "本该抛错")
    except SystemError as exc:
        check("重连不可用时把原始错误交出去", "ApplicationBase_Save" in str(exc), str(exc))
finally:
    originlab._reset_connection = _real_reset

print("超时后不执行排队任务")
import threading as _threading
import time as _time

_saved_abandoned = (originlab._abandoned[0], originlab._inflight[0])
_ran = []


def _sleepy():
    _time.sleep(3)
    _ran.append(1)
    return "late"


originlab._abandoned[0] = None
try:
    try:
        originlab.call(_sleepy, timeout=0.2)
        check("超时抛出", False, "本该超时")
    except originlab.OriginError as exc:
        check("超时报 com_timeout 且状态未知", exc.code == "com_timeout"
              and exc.extra.get("executed") == "unknown", (exc.code, exc.extra))
    # 任务超时后仍在跑：下一次调用必须立刻被拒，而不是悄悄排队
    try:
        originlab.call(lambda: "second", timeout=5)
        check("超时未完成时拒绝新任务", False, "本该拒绝")
    except originlab.OriginError as exc:
        check("超时未完成时拒绝新任务", exc.code == "origin_busy", exc.code)
    _time.sleep(3.2)
    check("被放弃的任务最终执行完（状态已如实告知）", _ran == [1], _ran)
    check("worker 空闲后恢复正常", originlab.call(lambda: "ok") == "ok")
finally:
    originlab._abandoned[0], originlab._inflight[0] = _saved_abandoned

print("超时会取消还没开始的任务")


def _queued_not_started():
    order = []

    def blocker():
        _time.sleep(1.5)
        return "blocked"

    t = _threading.Thread(target=lambda: order.append(originlab.call(blocker)))
    t.start()
    _time.sleep(0.2)                    # blocker 已在 worker 上跑
    try:
        originlab.call(lambda: order.append("must-not-run"), timeout=0.2)
    except originlab.OriginError as exc:
        check("排队任务超时报 com_timeout", exc.code == "com_timeout", exc.code)
    t.join()
    _time.sleep(0.6)                    # 给 worker 跳过被取消任务的时间
    check("被取消的排队任务没有执行", "must-not-run" not in order, order)


_queued_not_started()

print("MCP 取消：排队任务与执行中任务")


def _cancel_queued():
    ran = []
    ev = _threading.Event()

    def blocker():
        _time.sleep(1.5)
        return "blocked"

    t = _threading.Thread(target=lambda: originlab.call(blocker, timeout=10))
    t.start()
    _time.sleep(0.2)
    originlab.set_cancel_event(ev)
    ev.set()                      # what the server's reader does on the notification
    t0 = _time.time()
    try:
        originlab.call(lambda: ran.append(1) or "victim", timeout=10)
        check("排队任务可被取消", False, "本该取消")
    except originlab.OriginError as exc:
        check("排队任务报 cancelled", exc.code == "cancelled", exc.code)
        check("排队取消说明未执行", exc.extra.get("executed") in ("cancelled", "queued"),
              exc.extra)
        check("排队取消立即返回（不等 10 秒）", _time.time() - t0 < 3, _time.time() - t0)
    finally:
        originlab.set_cancel_event(None)
        t.join()
    _time.sleep(0.4)
    check("被取消的排队任务没有执行", ran == [], ran)


_cancel_queued()


def _cancel_inflight():
    ev = _threading.Event()
    ran = []
    setter = _threading.Thread(target=lambda: (_time.sleep(0.25), ev.set()))
    setter.start()
    originlab.set_cancel_event(ev)
    t0 = _time.time()

    def slow():
        _time.sleep(1.2)
        ran.append(1)
        return "late"

    try:
        originlab.call(slow, timeout=10)
        check("执行中任务可被取消", False, "本该取消")
    except originlab.OriginError as exc:
        check("执行中任务报 cancelled", exc.code == "cancelled", exc.code)
        check("执行中取消如实报 unknown", exc.extra.get("executed") == "unknown", exc.extra)
        check("取消立即返回（不等慢任务结束）", _time.time() - t0 < 1.0, _time.time() - t0)
    finally:
        originlab.set_cancel_event(None)
        setter.join()
    _time.sleep(1.4)
    check("无法停止的执行中任务仍会跑完（已如实告知）", ran == [1], ran)
    check("worker 空闲后恢复", originlab.call(lambda: "ok") == "ok")


_cancel_inflight()


def _cancel_event_cleared():
    ev = _threading.Event()
    ev.set()
    originlab.set_cancel_event(ev)
    originlab.set_cancel_event(None)
    check("清掉取消事件后 call 不受影响", originlab.call(lambda: 42) == 42)


_cancel_event_cleared()

print("server 工具注册")
import server  # noqa: E402

names = [t["name"] for t in server.TOOLS]
check("origin_instances 已注册", "origin_instances" in names, names[-3:])
check("origin_reclaim 已注册", "origin_reclaim" in names, names[-3:])
check("工具总数 28", len(names) == 28, len(names))
check("工具名唯一", len(set(names)) == len(names))
for tool in server.TOOLS:
    if tool["name"] in ("origin_instances", "origin_reclaim"):
        check("%s 有描述和 schema" % tool["name"],
              bool(tool.get("description")) and isinstance(tool.get("inputSchema"), dict))

print()
print("page-growth hint（不碰 COM）:")
originlab._reset_growth()
early = [originlab._growth_warning() for _ in range(49)]
check("前 49 个页面不出提示", all(h is None for h in early))
h50 = originlab._growth_warning()
check("第 50 个页面给出提示", bool(h50) and "50" in h50, h50)
check("提示带实测耗时", bool(h50) and "2.8" in h50, h50)
originlab._reset_growth()
check("清理后计数归零", originlab._page_growth[0] == 0, originlab._page_growth[0])
check("非新建不计数", originlab._growth_warning(created=False) is None
      and originlab._page_growth[0] == 0)
originlab._reset_growth()

print()
print("cross-session lock（不碰 COM，用临时锁路径）:")
import json as _json
import subprocess as _sp
import tempfile as _tempfile

_real_lock = originlab._SESSION_LOCK
_real_lock_dir = originlab._SESSION_DIR
_lockdir = _tempfile.mkdtemp(prefix="origin_bridge_locktest_")
originlab._SESSION_DIR = _lockdir
originlab._SESSION_LOCK = os.path.join(_lockdir, "session.lock")
try:
    holder = originlab._session_lock_acquire()
    check("首次获取成功", holder is None, holder)
    check("锁文件写着本进程 pid", _json.loads(open(originlab._SESSION_LOCK).read())["bridge_pid"] == os.getpid())

    child = (
        "import os, sys, json\n"
        "sys.path.insert(0, os.environ['BRIDGE_DIR'])\n"
        "import originlab\n"
        "originlab._SESSION_DIR = os.environ['LOCK_DIR']\n"
        "originlab._SESSION_LOCK = os.environ['LOCK_PATH']\n"
        "print(json.dumps(originlab._session_lock_acquire()))\n"
    )
    env = dict(os.environ, BRIDGE_DIR=os.path.dirname(os.path.abspath(__file__)),
               LOCK_DIR=_lockdir, LOCK_PATH=originlab._SESSION_LOCK)
    proc = _sp.run([sys.executable, "-c", child], capture_output=True, text=True,
                   encoding="utf-8", env=env, timeout=60)
    got = _json.loads(proc.stdout.strip().splitlines()[-1]) if proc.returncode == 0 else {}
    check("另一个活进程被拒绝并拿到本进程 pid",
          isinstance(got, dict) and got.get("bridge_pid") == os.getpid(), (proc.returncode, got))

    originlab._session_lock_release()
    check("释放后锁文件消失", not os.path.exists(originlab._SESSION_LOCK))

    dead = {"bridge_pid": 999999999, "started_at": "2000-01-01T00:00:00"}
    with open(originlab._SESSION_LOCK, "w", encoding="utf-8") as fh:
        _json.dump(dead, fh)
    holder = originlab._session_lock_acquire()
    check("死进程的旧锁被接管", holder is None, holder)
    originlab._session_lock_release()

    # P0-1 回归：半写入/损坏的锁不是「死锁」，不许抢。
    for label, blob in (("空文件（写一半的窗口）", ""), ("坏 JSON", "{not json"),
                        ("缺 bridge_pid", '{"started_at": "x"}')):
        with open(originlab._SESSION_LOCK, "w", encoding="utf-8") as fh:
            fh.write(blob)
        holder = originlab._session_lock_acquire()
        check("%s 被拒绝而不是抢占" % label,
              isinstance(holder, dict) and holder.get("error") == "lock_unreadable", holder)
        check("%s 保持原样" % label,
              open(originlab._SESSION_LOCK, encoding="utf-8").read() == blob)
        os.remove(originlab._SESSION_LOCK)
    check("未知 pid（None/0/非整数）视为存活",
          originlab._pid_alive(None) and originlab._pid_alive(0)
          and originlab._pid_alive("123"))

    # 锁目录写不了时 fail closed，而不是降级放行。
    blocker = os.path.join(_lockdir, "not_a_dir")
    with open(blocker, "w") as fh:
        fh.write("x")
    originlab._SESSION_DIR = blocker
    originlab._SESSION_LOCK = os.path.join(blocker, "session.lock")
    holder = originlab._session_lock_acquire()
    check("锁目录不可写时拒绝驱动（fail closed）",
          isinstance(holder, dict) and holder.get("error") == "lock_unavailable", holder)
    originlab._SESSION_DIR = _lockdir
    originlab._SESSION_LOCK = os.path.join(_lockdir, "session.lock")

    # 两个进程同时抢同一把死锁：rename 仲裁只允许一个赢家。
    race_child = (
        "import os, sys, json, time\n"
        "sys.path.insert(0, os.environ['BRIDGE_DIR'])\n"
        "import originlab\n"
        "originlab._SESSION_DIR = os.environ['LOCK_DIR']\n"
        "originlab._SESSION_LOCK = os.environ['LOCK_PATH']\n"
        "go = os.environ['GO_PATH']\n"
        "while not os.path.exists(go):\n"
        "    time.sleep(0.01)\n"
        "r = originlab._session_lock_acquire()\n"
        "time.sleep(2.0)\n"
        "print(json.dumps(None if r is None else {'bridge_pid': r.get('bridge_pid'),\n"
        "                                          'error': r.get('error')}))\n"
    )
    race_env = dict(env, GO_PATH=os.path.join(_lockdir, "go"))
    for round_no in (1, 2):
        with open(originlab._SESSION_LOCK, "w", encoding="utf-8") as fh:
            _json.dump(dead, fh)
        go = os.path.join(_lockdir, "go")
        if os.path.exists(go):
            os.remove(go)
        procs = [_sp.Popen([sys.executable, "-c", race_child], stdout=_sp.PIPE,
                           stderr=_sp.PIPE, text=True, encoding="utf-8", env=race_env)
                 for _ in range(2)]
        open(go, "w").close()                       # simultaneous start
        outs = [p.communicate(timeout=90)[0] for p in procs]
        results = [_json.loads(o.strip().splitlines()[-1]) for o in outs if o.strip()]
        winners = [r for r in results if r is None]
        check("第 %d 轮抢旧锁只有一个赢家" % round_no,
              len(results) == 2 and len(winners) == 1, results)
        if os.path.exists(originlab._SESSION_LOCK):
            os.remove(originlab._SESSION_LOCK)
finally:
    originlab._SESSION_LOCK = _real_lock
    originlab._SESSION_DIR = _real_lock_dir
    import shutil as _shutil
    _shutil.rmtree(_lockdir, ignore_errors=True)

print()
print("_close_pid 轮询时持续消解 busy 弹窗（桩，不碰 COM）:")
_saved_close = (originlab._process_rows, originlab._terminate, originlab._dismiss_busy_dialogs)
_log = []
_polls = {"n": 0}


def _fake_rows():
    _polls["n"] += 1
    return [{"pid": 4242, "started": "", "window": 0}] if _polls["n"] < 3 else []


def _fake_term(pid, force=False):
    _log.append("force" if force else "term")


def _fake_sweep(pid):
    _log.append("sweep")
    return []


try:
    originlab._process_rows, originlab._terminate, originlab._dismiss_busy_dialogs = (
        _fake_rows, _fake_term, _fake_sweep)
    _out = originlab._close_pid(4242)
finally:
    originlab._process_rows, originlab._terminate, originlab._dismiss_busy_dialogs = _saved_close

check("轮询期每轮都消解、干净退出",
      _out == "closed" and _log.count("sweep") >= 2 and _log[0] == "term", (_out, _log))

print()
print("busy-dialog finder（真窗口，不碰 COM）:")
if os.name == "nt":
    import subprocess as _sp
    import time as _time
    # A real #32770 message box owned by another process, carrying the same text
    # Origin uses for "另一个程序控制". MessageBox blocks the child until clicked,
    # so the child self-closes after 30 s as a safety net.
    child = (
        "import ctypes, sys, threading\n"
        "t = threading.Timer(30.0, lambda: ctypes.windll.user32.PostMessageW(None, 0x0010, 0, 0))\n"
        "t.daemon = True; t.start()\n"
        "ctypes.windll.user32.MessageBoxW(None, u'Origin 正由另一个程序控制，目前无法关闭', u'OriginPro 2024', 0x30)\n"
    )
    box = _sp.Popen([sys.executable, "-c", child])
    try:
        found = []
        for _ in range(40):           # up to 10 s for the window to appear
            _time.sleep(0.25)
            found = originlab._busy_dialogs(box.pid)
            if found:
                break
        check("找到忙对话框（正文在子 Static 里）",
              bool(found) and "另一个程序控制" in found[0]["body"], found)
        check("不误报无对话框进程", originlab._busy_dialogs(box.pid + 999999) == [])
        dismissed = originlab._dismiss_busy_dialogs(box.pid)
        _time.sleep(0.6)
        check("点掉后枚举不到", originlab._busy_dialogs(box.pid) == [], dismissed)
        check("点掉返回了正文", bool(dismissed) and "另一个程序控制" in dismissed[0], dismissed)
    finally:
        box.terminate()
else:
    print("  skip  非 Windows")

print()
if fails:
    print("%d 项失败: %s" % (len(fails), ", ".join(fails)))
    raise SystemExit(1)
print("all lifecycle checks passed")
