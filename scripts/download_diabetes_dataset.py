"""Download the Diabetes 130-US hospitals dataset used by the project."""

import argparse
import csv
import os
import tempfile
from pathlib import Path

import requests

DATASET_URL = (
    "https://docs.google.com/uc?export=download&confirm=t"
    "&id=1k5-1caezQ3zWJbKaiMULTGq-3sz6uThC"
)
DATASET_PATH = Path(__file__).resolve().parents[1] / "data" / "Diabetes" / "Diabetes.csv"
REQUIRED_COLUMNS = {"encounter_id", "readmitted"}
CHUNK_SIZE = 1024 * 1024


def validate_csv(path: Path) -> tuple[list[str], int]:
    """Check the expected dataset columns and count its records."""
    with path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        rows = csv.reader(csv_file)
        try:
            columns = next(rows)
        except StopIteration as exc:
            raise ValueError(f"Dataset is empty: {path}") from exc

        missing_columns = REQUIRED_COLUMNS.difference(columns)
        if missing_columns:
            missing = ", ".join(sorted(missing_columns))
            raise ValueError(f"Dataset is missing required columns: {missing}")

        row_count = sum(1 for _ in rows)
        if row_count == 0:
            raise ValueError(f"Dataset has a header but no records: {path}")

    return columns, row_count


def download_dataset(
    destination: Path = DATASET_PATH,
    force: bool = False,
    url: str = DATASET_URL,
) -> None:
    """Download the dataset atomically and validate the resulting CSV."""
    destination.parent.mkdir(parents=True, exist_ok=True)

    if destination.exists() and not force:
        columns, row_count = validate_csv(destination)
        print(f"Dataset already exists: {destination}")
        print(f"Records: {row_count:,} | Columns: {len(columns)}")
        print("Use --force to download it again.")
        return

    temporary_path: Path | None = None
    try:
        with requests.get(
            url,
            headers={"User-Agent": "diabetes-mlops-dataset-downloader"},
            stream=True,
            timeout=(15, 120),
        ) as response:
            response.raise_for_status()
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=destination.parent, suffix=".part", delete=False
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                    if not chunk:
                        continue
                    temporary_file.write(chunk)

        temporary_path.chmod(0o644)
        columns, row_count = validate_csv(temporary_path)
        os.replace(temporary_path, destination)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

    print(f"Dataset downloaded to: {destination}")
    print(f"Records: {row_count:,} | Columns: {len(columns)}")
    print("Target column: readmitted")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force",
        action="store_true",
        help="download again and replace the existing dataset",
    )
    args = parser.parse_args()
    download_dataset(force=args.force)


if __name__ == "__main__":
    main()
