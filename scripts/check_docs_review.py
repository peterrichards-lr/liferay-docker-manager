import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

FOOTER_REGEX = re.compile(
    r"\*Last Updated: ([\d\-]+)\* \| \*Last Reviewed: ([\d\-]+)\*"
)


def check_docs(max_review_days, max_update_days, max_gap_days):
    now = datetime.now()
    md_files = [str(p) for p in Path().rglob("*.md")]

    needs_review: list[tuple[str, str, int | None, int | None]] = []

    for file_path in md_files:
        # Ignore virtual environments, node_modules, etc.
        if any(
            ignored in file_path
            for ignored in [
                "/.venv/",
                "/node_modules/",
                "/e2e-work-dir/",
                "/.smoke_venv/",
            ]
        ) or file_path.startswith("."):
            continue

        with open(file_path, encoding="utf-8") as f:
            content = f.read()

        match = FOOTER_REGEX.search(content)
        if not match:
            needs_review.append((file_path, "No timestamp footer found", None, None))
            continue

        updated_str = match.group(1)
        reviewed_str = match.group(2)

        try:
            updated_dt = datetime.strptime(updated_str, "%Y-%m-%d")
            reviewed_dt = datetime.strptime(reviewed_str, "%Y-%m-%d")
        except ValueError:
            needs_review.append((file_path, "Invalid date format", None, None))
            continue

        days_since_update = (now - updated_dt).days
        days_since_review = (now - reviewed_dt).days
        gap_days = (updated_dt - reviewed_dt).days

        reasons = []
        if max_update_days is not None and days_since_update > max_update_days:
            reasons.append(
                f"{days_since_update} days since last update (max {max_update_days})"
            )
        if max_review_days is not None and days_since_review > max_review_days:
            reasons.append(
                f"{days_since_review} days since last review (max {max_review_days})"
            )
        if max_gap_days is not None and gap_days > max_gap_days:
            reasons.append(
                f"{gap_days} days between update and review (max {max_gap_days})"
            )

        if reasons:
            needs_review.append(
                (file_path, ", ".join(reasons), days_since_update, days_since_review)
            )

    if not needs_review:
        print("All documents are up to date and reviewed.")
        return 0

    print(f"Found {len(needs_review)} document(s) needing review:\n")
    for doc in needs_review:
        print(f"📄 {doc[0]}")
        print(f"   Reason: {doc[1]}")

    return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Check documentation review status.")
    parser.add_argument(
        "--max-review-days", type=int, help="Maximum days allowed since last review."
    )
    parser.add_argument(
        "--max-update-days", type=int, help="Maximum days allowed since last update."
    )
    parser.add_argument(
        "--max-gap-days",
        type=int,
        help="Maximum days allowed between update and review.",
    )

    args = parser.parse_args()

    if (
        args.max_review_days is None
        and args.max_update_days is None
        and args.max_gap_days is None
    ):
        parser.error(
            "At least one criteria must be specified (--max-review-days, --max-update-days, or --max-gap-days)"
        )

    sys.exit(check_docs(args.max_review_days, args.max_update_days, args.max_gap_days))
