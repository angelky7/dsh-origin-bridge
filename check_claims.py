"""Hold the READMEs to the numbers the shipped suites actually print.

The 0.4.11 round shipped README claims that had drifted from reality (binio's
check count depends on whether real workstation exports exist on the machine;
edge_checks gained cases nobody propagated). This script re-measures every
Origin-free suite, then compares each per-suite number mentioned in
README.md / README.en.md against the measurement, and checks the stated
grand total against the sum of the per-suite list.

Run:  python check_claims.py        # exit 1 on any mismatch

smoke_test.py / edge_checks.py need Origin and are skipped here (CI has none);
their counts are still covered by the total-arithmetic check.
"""

import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SUITES = ["test_asciiio", "test_binio", "test_lifecycle", "test_paths",
          "test_json_safety", "test_readfile", "test_labtalk_guard"]
# labels used in the READMEs -> suite module
LABELS = {"asciiio": "test_asciiio", "binio": "test_binio",
          "lifecycle": "test_lifecycle", "paths": "test_paths",
          "json": "test_json_safety", "readfile": "test_readfile",
          "labtalk": "test_labtalk_guard"}
CHECK_RE = re.compile(r"^\s*(?:ok|PASS)\b", re.MULTILINE)
problems = []


def measure(suite):
    proc = subprocess.run([sys.executable, "-u", os.path.join(HERE, suite + ".py")],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=600, cwd=HERE)
    if proc.returncode != 0:
        problems.append("%s 本身没跑过（exit %d）" % (suite, proc.returncode))
        return None
    return len(CHECK_RE.findall(proc.stdout))


def claimed_numbers(readme):
    """Every number the READMEs attach to each suite label, plus totals."""
    text = open(readme, encoding="utf-8").read()
    per_suite = {}
    for label in LABELS:
        nums = [int(m.group(1)) for m in
                re.finditer(r"\b%s\s+(\d+)" % label, text)]
        for m in re.finditer(r"test_%s\.py\s*#\s*(\d+)\s*(?:项|checks)" % label, text):
            nums.append(int(m.group(1)))
        per_suite[label] = nums
    for mod in ("smoke_test", "edge_checks"):
        label = "smoke" if mod == "smoke_test" else "edge"
        per_suite[label] = [int(m.group(1)) for m in
                            re.finditer(r"%s\.py\s*#\s*(\d+)\s*(?:项|checks)" % mod, text)]
    totals = []
    for m in re.finditer(
            r"(?:\*\*(\d+)\*\* offline|([0-9]+) ?项\*\*离线|这 ([0-9]+) 项随包发布"
            r"|# ([0-9]+) 项离线与单元检查|# ([0-9]+) offline)", text):
        totals.append(int(next(g for g in m.groups() if g)))
    return per_suite, totals


def main():
    measured = {}
    for suite in SUITES:
        n = measure(suite)
        measured[suite] = n
        print("%-18s %s" % (suite, n if n is not None else "FAILED"))

    for readme in ("README.md", "README.en.md"):
        path = os.path.join(HERE, readme)
        if not os.path.isfile(path):
            problems.append("%s 不见了" % readme)
            continue
        per_suite, totals = claimed_numbers(path)
        for label, nums in per_suite.items():
            if not nums:
                problems.append("%s：没有任何 %s 的数量声明（漏写了？）" % (readme, label))
                continue
            suite = LABELS.get(label)
            want = measured.get(suite)
            if want is None:
                continue          # smoke/edge: not measurable here
            for n in nums:
                if n != want:
                    problems.append("%s：%s 声明为 %d，实测 %d" % (readme, label, n, want))
        # grand total = sum of the per-suite list, wherever a total is stated
        s = sum(nums[0] for nums in per_suite.values() if nums)
        for t in totals:
            if t != s:
                problems.append("%s：总数声明为 %d，按分项加起来是 %d" % (readme, t, s))
        if not totals:
            problems.append("%s：找不到总数声明" % readme)

    print()
    if problems:
        for p in problems:
            print("  CLAIM-MISMATCH:", p)
        return 1
    print("all README coverage claims match the measured suites")
    return 0


if __name__ == "__main__":
    sys.exit(main())
