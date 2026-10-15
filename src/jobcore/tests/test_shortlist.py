import csv
from datetime import date, timedelta

from src.jobcore.shortlist import build_shortlist


def _body(title, company, location="Tel Aviv", domain="Software"):
    return f"*{title}* / {company}\n_{location}_ | _{domain}_\nhttps://x.co/1"


def _write(path, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["MessageBody", "UserName", "Date1", "Date2"])
        d = (date.today() - timedelta(days=5)).strftime("%Y-%m-%d")
        for body in rows:
            w.writerow([body, "Referally Bot", "", d])


def test_build_shortlist_ranked_and_slugged(tmp_path):
    csvp = tmp_path / "Referally Leadership sl.csv"
    _write(
        csvp,
        [
            _body("Software Team Lead", "Plain Co"),
            _body("LLM Team Lead", "AI Co"),
        ],
    )
    jobs = build_shortlist(csvp, from_scratch=True)
    assert len(jobs) == 2
    # ranked desc, AI-leadership on top with slug + score present
    assert jobs[0]["title"] == "LLM Team Lead"
    assert jobs[0]["company_slug"] == "ai-co"
    assert all("match_score" in j and "company_slug" in j for j in jobs)
