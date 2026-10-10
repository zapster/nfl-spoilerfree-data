#!/usr/bin/env python3
"""Build and validate the published nflverse season snapshots."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


GITHUB_REPO = "nflverse/nflverse-data"
GAMES_TAG = "schedules"
TEAMS_TAG = "teams"
GAMES_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/"
    "schedules/games.csv"
)
TEAMS_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/"
    "teams/teams_colors_logos.csv"
)
GAMES_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/tags/{GAMES_TAG}"
TEAMS_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/tags/{TEAMS_TAG}"
GAMES_REQUIRED = {
    "game_id",
    "season",
    "game_type",
    "gameday",
    "gametime",
    "away_team",
    "home_team",
    "away_score",
    "home_score",
}
SEASON_FILE_RE = re.compile(r"seasons/[0-9]{4}-[0-9a-f]{64}\.csv\Z")
TEAMS_FILE_RE = re.compile(r"teams-[0-9a-f]{64}\.csv\Z")
USER_AGENT = "nfl-spoilerfree-data-updater"


class UpdateError(RuntimeError):
    """An upstream, validation, or previous-publication error."""


def request_bytes(url: str, *, accept: str = "application/octet-stream") -> bytes:
    headers = {
        "Accept": accept,
        "User-Agent": USER_AGENT,
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token and urllib.parse.urlsplit(url).hostname == "api.github.com":
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        url,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            return response.read()
    except urllib.error.HTTPError:
        raise
    except (urllib.error.URLError, TimeoutError) as error:
        raise UpdateError(f"Request failed for {url}: {error}") from error


def request_json(url: str) -> dict[str, Any]:
    try:
        return json.loads(request_bytes(url, accept="application/vnd.github+json"))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise UpdateError(f"Invalid JSON returned by {url}: {error}") from error


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def source_release(api_url: str, source_url: str, tag: str, asset_name: str) -> dict[str, Any]:
    release = request_json(api_url)
    assets = {asset.get("name"): asset for asset in release.get("assets", [])}
    asset = assets.get(asset_name)
    if asset is None:
        raise UpdateError(f"Release {tag!r} does not contain {asset_name!r}")

    timestamp_asset = assets.get("timestamp.json") or assets.get("timestamp.txt")
    asset_digest = asset.get("digest")
    if asset_digest:
        signal = {"kind": "asset-digest", "value": asset_digest}
    elif timestamp_asset and timestamp_asset.get("digest"):
        signal = {
            "kind": f"{timestamp_asset['name']}-digest",
            "value": timestamp_asset["digest"],
        }
    else:
        timestamp = (timestamp_asset or asset).get("updated_at")
        if not timestamp:
            raise UpdateError(f"Release {tag!r} has no usable update signal for {asset_name}")
        signal = {"kind": "asset-updated-at", "value": timestamp}

    return {
        "url": source_url,
        "releaseMetadataUrl": api_url,
        "releaseTag": tag,
        "asset": asset_name,
        "assetDigest": asset_digest,
        "changeSignal": signal,
        "upstreamUpdatedAt": asset.get("updated_at") or release.get("published_at"),
    }


def download_source(source: dict[str, Any]) -> bytes:
    data = request_bytes(source["url"])
    expected = source.get("assetDigest")
    if expected:
        algorithm, separator, value = expected.partition(":")
        if not separator or algorithm != "sha256":
            raise UpdateError(f"Unsupported upstream asset digest: {expected}")
        actual = sha256(data)
        if actual != value:
            raise UpdateError(
                f"Downloaded {source['asset']} SHA-256 mismatch: expected {value}, got {actual}"
            )
    return data


def parse_csv(data: bytes, label: str) -> tuple[list[str], list[dict[str, str]]]:
    try:
        text = io.TextIOWrapper(io.BytesIO(data), encoding="utf-8-sig", newline="")
        reader = csv.DictReader(text)
        fieldnames = reader.fieldnames
        if not fieldnames or any(not field for field in fieldnames):
            raise UpdateError(f"{label} has no valid CSV header")
        if len(fieldnames) != len(set(fieldnames)):
            raise UpdateError(f"{label} has duplicate CSV column names")
        rows = list(reader)
    except (UnicodeDecodeError, csv.Error) as error:
        raise UpdateError(f"Could not parse {label}: {error}") from error

    for line_number, row in enumerate(rows, start=2):
        if None in row or any(value is None for value in row.values()):
            raise UpdateError(f"{label} has a malformed row at line {line_number}")
    return fieldnames, rows


def validate_games(
    fieldnames: list[str], rows: list[dict[str, str]], team_abbreviations: set[str]
) -> tuple[dict[str, list[dict[str, str]]], dict[str, set[str]]]:
    missing_fields = sorted(GAMES_REQUIRED - set(fieldnames))
    if missing_fields:
        raise UpdateError(f"games.csv is missing required columns: {', '.join(missing_fields)}")

    seasons: dict[str, list[dict[str, str]]] = defaultdict(list)
    teams_by_season: dict[str, set[str]] = defaultdict(set)
    seen_game_ids: set[str] = set()
    referenced_teams: set[str] = set()

    for line_number, row in enumerate(rows, start=2):
        game_id = row["game_id"].strip()
        if not game_id:
            raise UpdateError(f"games.csv has an empty game_id at line {line_number}")
        if game_id in seen_game_ids:
            raise UpdateError(f"games.csv has duplicate game_id {game_id!r}")
        seen_game_ids.add(game_id)

        season = row["season"].strip()
        if not season.isdigit() or int(season) < 1900:
            raise UpdateError(f"games.csv has invalid season {season!r} at line {line_number}")
        game_type = row["game_type"].strip()
        if not game_type:
            raise UpdateError(f"games.csv has an empty game_type at line {line_number}")

        for score_field in ("away_score", "home_score"):
            score = row[score_field].strip()
            if score and not score.isdigit():
                raise UpdateError(
                    f"games.csv has invalid nullable score {score!r} in {game_id}"
                )

        game_teams = {row["away_team"].strip(), row["home_team"].strip()}
        if "" in game_teams:
            raise UpdateError(f"games.csv has a missing team in {game_id}")
        missing_teams = game_teams - team_abbreviations
        if missing_teams:
            raise UpdateError(
                f"games.csv references teams missing from team metadata: {', '.join(sorted(missing_teams))}"
            )
        referenced_teams.update(game_teams)
        teams_by_season[season].update(game_teams)
        seasons[season].append(row)

    if not seasons:
        raise UpdateError("games.csv contains no games")

    if "2025" in seasons and len(seasons["2025"]) != 285:
        raise UpdateError(
            f"Expected all 285 games in the known 2025 snapshot, found {len(seasons['2025'])}"
        )

    for season_rows in seasons.values():
        season_rows.sort(
            key=lambda row: (
                row["gameday"],
                row["gametime"],
                row["game_id"],
            )
        )
    return dict(seasons), dict(teams_by_season)


def validate_teams(fieldnames: list[str], rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    if "team_abbr" not in fieldnames:
        raise UpdateError("teams_colors_logos.csv is missing the team_abbr column")
    teams: dict[str, dict[str, str]] = {}
    for line_number, row in enumerate(rows, start=2):
        abbreviation = row["team_abbr"].strip()
        if not abbreviation:
            raise UpdateError(f"teams_colors_logos.csv has an empty team_abbr at line {line_number}")
        if abbreviation in teams:
            raise UpdateError(f"teams_colors_logos.csv has duplicate team_abbr {abbreviation!r}")
        teams[abbreviation] = row
    return teams


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def safe_previous_path(path: str, pattern: re.Pattern[str]) -> str:
    if not pattern.fullmatch(path):
        raise UpdateError(f"Unexpected published file path in manifest: {path!r}")
    if PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts:
        raise UpdateError(f"Unsafe published file path in manifest: {path!r}")
    return path


def previous_file(
    manifest_url: str,
    entry: dict[str, Any],
    output_dir: Path,
    pattern: re.Pattern[str],
) -> bytes:
    relative_path = safe_previous_path(entry.get("path", ""), pattern)
    url = urllib.parse.urljoin(manifest_url, relative_path)
    data = request_bytes(url)
    actual = sha256(data)
    if actual != entry.get("sha256"):
        raise UpdateError(
            f"Previously published file failed hash validation: {relative_path}"
        )
    destination = output_dir / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return data


def read_previous_manifest(manifest_url: str) -> dict[str, Any] | None:
    cache_busted = f"{manifest_url}?_={int(datetime.now(timezone.utc).timestamp())}"
    try:
        data = request_bytes(cache_busted, accept="application/json")
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise UpdateError(f"Could not read the currently published manifest: HTTP {error.code}") from error
    try:
        manifest = json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise UpdateError(f"The currently published manifest is invalid JSON: {error}") from error
    if not isinstance(manifest, dict) or manifest.get("manifestVersion") != 1:
        raise UpdateError("The currently published manifest has an unsupported format")
    if not isinstance(manifest.get("seasons"), dict) or not isinstance(manifest.get("source"), dict):
        raise UpdateError("The currently published manifest is missing required fields")
    return manifest


def write_github_outputs(deploy: bool) -> None:
    output_path = Path(os.environ.get("GITHUB_OUTPUT", ""))
    if output_path.is_file():
        with output_path.open("a", encoding="utf-8") as output:
            output.write(f"deploy={'true' if deploy else 'false'}\n")


def write_summary(message: str) -> None:
    summary_path = Path(os.environ.get("GITHUB_STEP_SUMMARY", ""))
    if summary_path.is_file():
        with summary_path.open("a", encoding="utf-8") as summary:
            summary.write(message.rstrip() + "\n")


def publish(args: argparse.Namespace) -> bool:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(__file__).resolve().parents[1] / "index.html", output_dir / "index.html")
    previous = read_previous_manifest(args.manifest_url)

    sources = {
        "games": source_release(GAMES_RELEASE_API, GAMES_URL, GAMES_TAG, "games.csv"),
        "teams": source_release(TEAMS_RELEASE_API, TEAMS_URL, TEAMS_TAG, "teams_colors_logos.csv"),
    }

    if previous is None:
        changed = set(sources)
    else:
        old_sources = previous["source"]
        changed = {
            name
            for name, source in sources.items()
            if name not in old_sources
            or old_sources[name].get("changeSignal") != source["changeSignal"]
        }

    if not changed:
        message = "No upstream source release changed. No CSV files were downloaded; Pages deployment skipped."
        print(message)
        write_summary(f"## nflverse data update\n\n{message}")
        write_github_outputs(False)
        return False

    print(f"Changed sources: {', '.join(sorted(changed))}")

    if "games" in changed:
        games_data = download_source(sources["games"])
        game_fields, game_rows = parse_csv(games_data, "games.csv")
    else:
        assert previous is not None
        game_fields = []
        game_rows = []

    if "teams" in changed:
        teams_data = download_source(sources["teams"])
        team_fields, team_rows = parse_csv(teams_data, "teams_colors_logos.csv")
        team_by_abbr = validate_teams(team_fields, team_rows)
    else:
        assert previous is not None
        teams_entry = previous.get("teams")
        if not isinstance(teams_entry, dict):
            raise UpdateError("The current manifest has no teams file entry")
        teams_data = previous_file(manifest_url=args.manifest_url, entry=teams_entry, output_dir=output_dir, pattern=TEAMS_FILE_RE)
        team_fields, team_rows = parse_csv(teams_data, "previously published teams file")
        team_by_abbr = validate_teams(team_fields, team_rows)

    if "games" in changed:
        seasons, teams_by_season = validate_games(
            game_fields, game_rows, set(team_by_abbr)
        )
        season_entries: dict[str, dict[str, Any]] = {}
        for season in sorted(seasons, key=int):
            rows = seasons[season]
            destination = output_dir / f"seasons/{season}-pending.csv"
            write_csv(destination, game_fields, rows)
            data = destination.read_bytes()
            digest = sha256(data)
            relative_path = f"seasons/{season}-{digest}.csv"
            final_path = output_dir / relative_path
            if destination != final_path:
                final_path.parent.mkdir(parents=True, exist_ok=True)
                destination.replace(final_path)
            season_entries[season] = {
                "path": relative_path,
                "sha256": digest,
                "records": len(rows),
                "gameTypeCounts": dict(sorted(Counter(row["game_type"] for row in rows).items())),
                "teams": sorted(teams_by_season[season]),
            }
    else:
        assert previous is not None
        season_entries = previous["seasons"]
        for entry in season_entries.values():
            previous_file(
                manifest_url=args.manifest_url,
                entry=entry,
                output_dir=output_dir,
                pattern=SEASON_FILE_RE,
            )
        teams_by_season = {
            season: set(entry.get("teams", []))
            for season, entry in season_entries.items()
        }

    referenced_teams = set().union(*teams_by_season.values()) if teams_by_season else set()
    missing_teams = referenced_teams - set(team_by_abbr)
    if missing_teams:
        raise UpdateError(
            "Missing team metadata for game data: " + ", ".join(sorted(missing_teams))
        )

    if "teams" in changed:
        filtered_team_rows = [
            row for row in team_rows if row["team_abbr"].strip() in referenced_teams
        ]
        destination = output_dir / "teams-pending.csv"
        write_csv(destination, team_fields, filtered_team_rows)
        team_data = destination.read_bytes()
        team_hash = sha256(team_data)
        team_path = f"teams-{team_hash}.csv"
        final_team_path = output_dir / team_path
        destination.replace(final_team_path)
        teams_entry = {
            "path": team_path,
            "sha256": team_hash,
            "records": len(filtered_team_rows),
        }
    else:
        assert previous is not None
        teams_entry = previous["teams"]

    all_type_counts: Counter[str] = Counter()
    for entry in season_entries.values():
        all_type_counts.update(entry["gameTypeCounts"])
    manifest = {
        "manifestVersion": 1,
        "publicationFormatVersion": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "source": sources,
        "seasons": {season: season_entries[season] for season in sorted(season_entries, key=int)},
        "teams": teams_entry,
        "recordCounts": {
            "games": sum(entry["records"] for entry in season_entries.values()),
            "seasons": len(season_entries),
            "teams": teams_entry["records"],
            "gameTypeCounts": dict(sorted(all_type_counts.items())),
        },
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    deploy = previous is None or manifest != previous
    message = (
        "## nflverse data update\n\n"
        f"Changed sources: {', '.join(sorted(changed))}\n\n"
        f"Seasons: {len(season_entries)}; games: {manifest['recordCounts']['games']}; "
        f"teams: {teams_entry['records']}\n\n"
        f"Game types: `{json.dumps(manifest['recordCounts']['gameTypeCounts'], sort_keys=True)}`\n\n"
        f"Pages deployment {'required' if deploy else 'not required'}."
    )
    print(message)
    write_summary(message)
    write_github_outputs(deploy)
    return deploy


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest-url",
        default="https://zapster.github.io/nfl-spoilerfree-data/manifest.json",
        help="URL of the currently deployed manifest",
    )
    parser.add_argument("--output-dir", default="_site", help="Static-site output directory")
    args = parser.parse_args()
    try:
        publish(args)
    except (UpdateError, OSError, KeyError, TypeError, ValueError) as error:
        message = f"Data update failed: {error}"
        print(message, file=sys.stderr)
        write_summary(f"## nflverse data update failed\n\n{message}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
