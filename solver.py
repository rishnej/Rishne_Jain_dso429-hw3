"""DSO 429 HW3 hourly quiz solver.

Reads questions.csv, answers every round that has no answers/round-NNNN.json yet,
and writes those files. Each question is computed from its numbers and matched to
the closest option. Questions it can't parse are listed in unsolved.json for a
human/LLM to answer; their files are NOT written so nothing half-done gets pushed.

  python3 solver.py --questions questions.csv --out answers [--rounds 49-60]
  python3 solver.py --questions questions.csv --check practice_keys.csv
  python3 solver.py --questions questions.csv --out answers --rounds 61 --partial   (unknown questions -> "?")
  python3 solver.py --validate answers      (every round file: valid JSON, q1..q25 each A-D)
"""
import argparse
import csv
import json
import re
from datetime import date, timedelta
from pathlib import Path

NUM = r"\$?(-?[\d,]+(?:\.\d+)?)"
MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]


def n(s: str) -> float:
    return float(s.replace(",", "").replace("$", "").replace("%", "").replace("units", "").strip())


def parse_date(s: str) -> date | None:
    m = re.fullmatch(r"\s*(\d{1,2}) (\w+) (\d{4})\s*", s)
    if not m or m.group(2) not in MONTHS:
        return None
    return date(int(m.group(3)), MONTHS.index(m.group(2)) + 1, int(m.group(1)))


def next_in_series(xs: list[float]) -> float:
    d = [b - a for a, b in zip(xs, xs[1:])]
    if len(set(d)) == 1:
        return xs[-1] + d[0]
    if all(x != 0 for x in xs):
        r = [b / a for a, b in zip(xs, xs[1:])]
        if max(r) - min(r) < 1e-9:
            return xs[-1] * r[0]
    dd = [b - a for a, b in zip(d, d[1:])]
    if len(set(dd)) == 1:
        return xs[-1] + d[-1] + dd[0]
    if len(xs) >= 3 and all(xs[i] == xs[i - 1] + xs[i - 2] for i in range(2, len(xs))):
        return xs[-1] + xs[-2]
    raise ValueError("unknown series pattern")


def target(q: str):
    """The correct value for a question: a number, or a date for settlement questions."""
    rules = [
        (rf"investment of {NUM} grows at {NUM}% compounded annually.*after {NUM} years",
         lambda p, r, t: p * (1 + r / 100) ** t),
        (rf"generates {NUM} in revenue against {NUM} in cost",
         lambda rev, cogs: (rev - cogs) / rev * 100),
        (rf"listed at {NUM} is discounted by {NUM}%",
         lambda p, d: p * (1 - d / 100)),
        (rf"sells {NUM} units at {NUM} each",
         lambda u, p: u * p),
        (rf"moved from {NUM} to {NUM}",
         lambda a, b: (b - a) / a * 100),
        (rf"Fixed costs are {NUM}\. Each unit sells for {NUM} with a variable cost of {NUM}",
         lambda f, p, v: f / (p - v)),
        (rf"Region A had {NUM} orders averaging {NUM}; region B had {NUM} orders averaging {NUM}",
         lambda na, a, nb, b: (na * a + nb * b) / (na + nb)),
        (rf"holds {NUM} billing tickets, {NUM} technical tickets and {NUM} account tickets",
         lambda b, t, a: b / (b + t + a) * 100),
        (rf"A {NUM} marketing budget is split between two channels in the ratio {NUM}:{NUM}",
         lambda tot, x, y: tot * x / (x + y)),
        (rf"invoice for {NUM} is converted to euros at {NUM} euros per US dollar",
         lambda usd, rate: usd * rate),
    ]
    for pat, fn in rules:
        m = re.search(pat, q)
        if m:
            return fn(*(n(g) for g in m.groups()))
    m = re.search(r"series runs ([\d,.\s-]+?)\. If", q)
    if m:
        return next_in_series([n(x) for x in m.group(1).split(", ")])
    m = re.search(r"signed on (\d{1,2} \w+ \d{4}) has a (\d+)-day settlement", q)
    if m:
        return parse_date(m.group(1)) + timedelta(days=int(m.group(2)))
    raise ValueError("unrecognised question type")


def pick(q: str, options: list[str]) -> str:
    t = target(q)
    if isinstance(t, date):
        dates = [parse_date(o) for o in options]
        if t not in dates:
            raise ValueError(f"computed date {t} not among options")
        return "ABCD"[dates.index(t)]
    vals = [n(o) for o in options]
    dist = [abs(v - t) for v in vals]
    best = min(dist)
    # sanity: the right option should be essentially exact (rounding only)
    if best > max(0.01 * abs(t), 0.051):
        raise ValueError(f"no option close to computed {t:.4f}: {options}")
    return "ABCD"[dist.index(best)]


def load(path):
    rounds = {}
    for r in csv.DictReader(open(path, newline="", encoding="utf-8")):
        rounds.setdefault(int(r["round_id"]), []).append(r)
    return rounds


def solve_round(rows):
    answers, problems = {}, []
    for r in sorted(rows, key=lambda r: int(r["q_index"])):
        opts = [r["option_a"], r["option_b"], r["option_c"], r["option_d"]]
        try:
            answers[f"q{int(r['q_index'])}"] = pick(r["question_text"], opts)
        except Exception as e:
            problems.append({"round_id": int(r["round_id"]), "q_index": int(r["q_index"]),
                             "question": r["question_text"], "options": dict(zip("ABCD", opts)), "why": str(e)})
    return answers, problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions")
    ap.add_argument("--out", default="answers")
    ap.add_argument("--rounds", help="e.g. 49-60; default = every round with no answer file yet")
    ap.add_argument("--check", help="practice_keys.csv: score instead of writing files")
    ap.add_argument("--partial", action="store_true", help='write "?" for questions it cannot solve')
    ap.add_argument("--validate", metavar="DIR", help="check answer files and exit 1 if any is bad")
    a = ap.parse_args()

    if a.validate:
        bad = []
        for f in sorted(Path(a.validate).glob("round-*.json")):
            try:
                d = json.loads(f.read_text())
                ans = d["answers"]
                ok = re.fullmatch(r"round-\d{4}\.json", f.name) and len(ans) == 25 and all(
                    ans.get(f"q{i}") in ("A", "B", "C", "D") for i in range(1, 26))
            except Exception:
                ok = False
            if not ok:
                bad.append(f.name)
        print(json.dumps({"bad_files": bad}))
        raise SystemExit(1 if bad else 0)

    rounds = load(a.questions)

    if a.check:
        keys = {(int(k["round_id"]), int(k["q_index"])): k["answer"] for k in csv.DictReader(open(a.check))}
        right = total = 0
        for rid, rows in rounds.items():
            ans, probs = solve_round(rows)
            for p in probs:
                print("UNSOLVED", p)
            for r in rows:
                key = keys.get((rid, int(r["q_index"])))
                if key is None:
                    continue
                total += 1
                got = ans.get(f"q{int(r['q_index'])}")
                right += got == key
                if got != key:
                    print("WRONG", rid, r["q_index"], got, key, r["question_text"])
        print(json.dumps({"checked": total, "correct": right}))
        return

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    if a.rounds:
        lo, _, hi = a.rounds.partition("-")
        todo = [r for r in rounds if int(lo) <= r <= int(hi or lo)]
    else:
        todo = [r for r in rounds if not (out / f"round-{r:04d}.json").exists()]
    written, unsolved = [], []
    for rid in sorted(todo):
        ans, probs = solve_round(rounds[rid])
        unsolved += probs
        if probs and a.partial:
            for p in probs:
                ans[f"q{p['q_index']}"] = "?"
            ans = dict(sorted(ans.items(), key=lambda kv: int(kv[0][1:])))
        elif probs or len(ans) != len(rounds[rid]):
            continue
        (out / f"round-{rid:04d}.json").write_text(json.dumps({"round_id": rid, "answers": ans}, indent=2) + "\n")
        written.append(rid)
    Path("unsolved.json").write_text(json.dumps(unsolved, indent=2))
    print(json.dumps({"written": written, "unsolved_questions": len(unsolved)}))


if __name__ == "__main__":
    main()
