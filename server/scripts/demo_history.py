"""DEMO ONLY: give a freshly seeded demo instance two weeks of plausible history, so the overview chart and
activity feeds look like a team that has been using it. It writes made-up audit entries dated in the past.
Never run it on a real instance.

Run after seed_demo.py, inside the app container (it uses the app's database settings):
    docker compose exec app python3 scripts/demo_history.py --yes-this-is-a-demo
"""
from __future__ import annotations

import datetime as dt
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import select  # noqa: E402

from app import config  # noqa: E402
from app.db import Audit, User, init_engine, new_session, utcnow  # noqa: E402

WORK = {
    "maya": ["/shared/platform-eng/dashboards/latency-dashboard.html", "/shared/platform-eng/projects/latency-svc/capacity-plan.md",
             "/shared/platform-eng/runbooks/incident-0412.md", "/users/maya/projects/perf-tests/perf-notes.md",
             "/shared/platform-eng/dashboards/error-budget.html"],
    "jonas": ["/shared/platform-eng/runbooks/on-call-checklist.md", "/shared/platform-eng/runbooks/latency-by-region.csv",
              "/shared/platform-eng/projects/latency-svc/rollout-plan.md"],
    "priya": ["/users/priya/projects/design-system/brand-tokens.json", "/shared/design/onboarding-flow.svg",
              "/shared/research/interviews/interview-synthesis.md", "/shared/design/specs/settings-page.html"],
    "sam": ["/shared/research/interviews/notes-week-44.md", "/shared/research/survey-results.csv"],
}


def main() -> None:
    if "--yes-this-is-a-demo" not in sys.argv:
        raise SystemExit(__doc__)
    init_engine(config.settings.database_url)
    rnd = random.Random(7)
    now = utcnow()
    n = 0
    with new_session() as db:
        users = {u.username: u for u in db.scalars(select(User))}
        for days_ago in range(13, 0, -1):
            day = (now - dt.timedelta(days=days_ago)).replace(hour=9, minute=0, second=0, microsecond=0)
            weekend = day.weekday() >= 5
            for who, files in WORK.items():
                u = users.get(who)
                if u is None:
                    continue
                for _ in range(rnd.randint(0, 1) if weekend else rnd.randint(1, 4)):
                    ts = day + dt.timedelta(minutes=rnd.randint(0, 9 * 60))
                    via = "mcp" if rnd.random() < 0.72 else "web"
                    path = rnd.choice(files)
                    db.add(Audit(ts=ts, user_id=u.id, actor=u.username, action="file.save", target=path, via=via, ok=True,
                                 detail={"version": rnd.randint(1, 6), "source": via, "demo": True}, ip="10.20.4." + str(rnd.randint(10, 90))))
                    n += 1
                    if rnd.random() < 0.5:
                        reader = rnd.choice([x for x in WORK if x != who])
                        r = users.get(reader)
                        if r:
                            db.add(Audit(ts=ts + dt.timedelta(minutes=rnd.randint(5, 240)), user_id=r.id, actor=r.username,
                                         action="file.read", target=path, via="web", ok=True, detail={"demo": True}))
                            n += 1
        db.commit()
    print(f"Added {n} demo history entries over the last 13 days.")


if __name__ == "__main__":
    main()
