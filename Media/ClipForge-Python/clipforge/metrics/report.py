"""Performance report from stored metrics, and the winning scripts fed back into the script prompt."""

import json
import re
from collections import defaultdict
from datetime import date, timedelta

from ..db import DB

BUCKET_S = 5


def hook_style(hook: str) -> str:
    text = hook.strip().lower()
    if text.endswith("?"):
        return "question"
    if re.search(r"\d", text):
        return "number"
    if re.match(r"(you|your)\b", text):
        return "second person"
    return "statement"


def length_bucket(duration_s: float) -> str:
    low = int(duration_s // BUCKET_S) * BUCKET_S
    return f"{low}-{low + BUCKET_S}s"


def post_rows(db: DB, since: date | None = None) -> list[dict]:
    """One row per post with its latest snapshot. Retention is average view time over clip length."""
    rows = db.all(
        """
        SELECT p.id AS post_row, p.job_id, p.platform, p.url, p.created_at, j.angle, r.duration_s, s.body,
               m.views, m.likes, m.shares, m.avg_view_duration_s, m.pulled_on
        FROM posts p
        JOIN jobs j ON j.id = p.job_id
        JOIN renders r ON r.job_id = p.job_id
        JOIN scripts s ON s.job_id = p.job_id
        JOIN metrics m ON m.post_id = p.id
        WHERE m.pulled_on = (SELECT MAX(pulled_on) FROM metrics WHERE post_id = p.id)
        ORDER BY p.created_at, p.id
        """
    )
    result = []
    for row in rows:
        if since and date.fromtimestamp(row["created_at"]) < since:
            continue
        item = dict(row)
        script = json.loads(item.pop("body"))
        sidecar_row = db.one("SELECT sidecar_path FROM renders WHERE job_id = ?", row["job_id"])
        try:
            template = json.loads(open(sidecar_row["sidecar_path"]).read())["template"]  # the one actually rendered
        except (OSError, KeyError, ValueError):
            template = db.job(row["job_id"])["template"]
        avg = item["avg_view_duration_s"]
        item.update(
            script=script,
            hook=script["hook"],
            template=template,
            retention=min(avg / item["duration_s"], 1.0) if avg is not None else None,
        )
        result.append(item)
    return result


def top_scripts(db: DB, count: int = 5) -> list[str]:
    """The best-retained scripts, as text for the script prompt."""
    rated = [row for row in post_rows(db) if row["retention"] is not None]
    best_per_job: dict[str, dict] = {}
    for row in rated:
        if row["job_id"] not in best_per_job or row["retention"] > best_per_job[row["job_id"]]["retention"]:
            best_per_job[row["job_id"]] = row
    ranked = sorted(best_per_job.values(), key=lambda r: (-r["retention"], r["job_id"]))[:count]
    return [
        f"({row['retention']:.0%} retention, {row['angle']}) {row['hook']} "
        + " ".join(line["text"] for line in row["script"]["lines"])
        for row in ranked
    ]


def _group_table(rows: list[dict], key, title: str) -> list[str]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        groups[key(row)].append(row)
    lines = [f"### By {title}", "", f"| {title.capitalize()} | Posts | Avg retention | Avg views |", "| --- | --- | --- | --- |"]

    def mean_retention(items):
        values = [i["retention"] for i in items if i["retention"] is not None]
        return sum(values) / len(values) if values else None

    ordered = sorted(groups.items(), key=lambda kv: (-(mean_retention(kv[1]) or -1), kv[0]))
    for name, items in ordered:
        retention = mean_retention(items)
        shown = f"{retention:.0%}" if retention is not None else "n/a"
        lines.append(f"| {name} | {len(items)} | {shown} | {sum(i['views'] for i in items) / len(items):.0f} |")
    return lines + [""]


def _post_line(row: dict) -> str:
    link = f"[{row['platform']}]({row['url']})" if row["url"] else row["platform"]
    return (
        f"- {row['retention']:.0%} retention, {row['views']} views, {row['likes']} likes, {row['shares']} shares "
        f"- \"{row['hook']}\" ({row['angle']}, {row['template']}, {row['duration_s']:.0f}s, {link})"
    )


def build_report(db: DB, today: date, days: int = 7) -> str:
    since = today - timedelta(days=days)
    rows = post_rows(db, since)
    lines = [f"# ClipForge report: {since.isoformat()} to {today.isoformat()}", ""]
    if not rows:
        return "\n".join(lines + ["No posts with metrics in this period. Run `clipforge metrics-pull` or `clipforge metrics-add`.", ""])
    lines += [f"{len(rows)} posts, {sum(r['views'] for r in rows)} views in total.", ""]

    rated = sorted((r for r in rows if r["retention"] is not None), key=lambda r: (-r["retention"], r["post_row"]))
    if rated:
        lines += ["## Best by retention", ""] + [_post_line(r) for r in rated[:3]] + [""]
        if len(rated) > 3:
            lines += ["## Worst by retention", ""] + [_post_line(r) for r in rated[::-1][:3]] + [""]
    else:
        lines += ["No post has an average view duration yet, so retention cannot be ranked.", ""]

    lines += ["## Breakdown", ""]
    lines += _group_table(rows, lambda r: r["angle"], "angle")
    lines += _group_table(rows, lambda r: r["template"], "template")
    lines += _group_table(rows, lambda r: length_bucket(r["duration_s"]), "clip length")
    lines += _group_table(rows, lambda r: hook_style(r["hook"]), "hook wording")

    since_ts = (today - timedelta(days=days)).toordinal()
    rejections = [
        row["reason"]
        for row in db.all("SELECT reason, created_at FROM reviews WHERE action = 'reject' AND reason IS NOT NULL ORDER BY id")
        if date.fromtimestamp(row["created_at"]).toordinal() >= since_ts
    ]
    if rejections:
        lines += ["## Rejection reasons", ""] + [f"- {reason}" for reason in rejections] + [""]
    return "\n".join(lines)
