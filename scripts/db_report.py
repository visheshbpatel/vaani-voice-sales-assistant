import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, select

from app.database import SessionLocal
from app.models.call import Call


def _status(value) -> str | None:
    return value.value if hasattr(value, "value") else value


def main() -> None:
    parser = argparse.ArgumentParser(description="Report or clean short Vaani calls.")
    parser.add_argument(
        "--clean",
        action="store_true",
        help="delete calls with duration_seconds less than two seconds",
    )
    args = parser.parse_args()

    with SessionLocal() as db:
        if args.clean:
            result = db.execute(
                delete(Call).where(Call.duration_seconds < 2)
            )
            db.commit()
            print(f"Deleted {result.rowcount} junk rows.")
            return

        calls = list(db.scalars(select(Call).order_by(Call.id)))
        for call in calls:
            transcript_length = len(call.transcript or [])
            print(
                f"id={call.id} started_at={call.started_at} "
                f"duration={call.duration_seconds} status={_status(call.status)} "
                f"summary_generation_status={_status(call.summary_generation_status)} "
                f"transcript_length={transcript_length}"
            )
        junk_count = sum(
            call.duration_seconds is not None and call.duration_seconds < 2
            for call in calls
        )
        print(f"Junk rows: {junk_count}")


if __name__ == "__main__":
    main()
