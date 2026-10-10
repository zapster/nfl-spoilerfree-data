import argparse
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from scripts.update_data import (
    UpdateError,
    publish,
    request_bytes,
    validate_games,
    validate_teams,
)


class ValidateDataTests(unittest.TestCase):
    def setUp(self):
        self.game_fields = [
            "game_id",
            "season",
            "game_type",
            "gameday",
            "gametime",
            "away_team",
            "home_team",
            "away_score",
            "home_score",
        ]
        self.game_rows = [
            {
                "game_id": "2099_01_A_AT_B",
                "season": "2099",
                "game_type": "REG",
                "gameday": "2099-09-01",
                "gametime": "13:00",
                "away_team": "A",
                "home_team": "B",
                "away_score": "",
                "home_score": "",
            },
            {
                "game_id": "2099_01_C_AT_D",
                "season": "2099",
                "game_type": "POST",
                "gameday": "2100-01-01",
                "gametime": "16:30",
                "away_team": "C",
                "home_team": "D",
                "away_score": "21",
                "home_score": "24",
            },
        ]

    def test_groups_every_game_type_by_discovered_season(self):
        seasons, teams = validate_games(self.game_fields, self.game_rows, {"A", "B", "C", "D"})

        self.assertEqual(set(seasons), {"2099"})
        self.assertEqual(len(seasons["2099"]), 2)
        self.assertEqual(teams["2099"], {"A", "B", "C", "D"})
        self.assertEqual({row["game_type"] for row in seasons["2099"]}, {"REG", "POST"})

    def test_rejects_duplicate_game_ids(self):
        duplicate_rows = [self.game_rows[0], dict(self.game_rows[0])]

        with self.assertRaisesRegex(UpdateError, "duplicate game_id"):
            validate_games(self.game_fields, duplicate_rows, {"A", "B"})

    def test_rejects_non_numeric_non_null_scores(self):
        bad_rows = [dict(self.game_rows[0], away_score="final")]

        with self.assertRaisesRegex(UpdateError, "invalid nullable score"):
            validate_games(self.game_fields, bad_rows, {"A", "B"})

    def test_rejects_missing_team_references(self):
        with self.assertRaisesRegex(UpdateError, "missing from team metadata"):
            validate_games(self.game_fields, self.game_rows[:1], {"A"})

    def test_rejects_duplicate_team_metadata(self):
        rows = [
            {"team_abbr": "A", "team_name": "A Team"},
            {"team_abbr": "A", "team_name": "Another A Team"},
        ]

        with self.assertRaisesRegex(UpdateError, "duplicate team_abbr"):
            validate_teams(["team_abbr", "team_name"], rows)

    def test_unchanged_sources_skip_csv_downloads_and_deployment(self):
        games_source = {"changeSignal": {"kind": "asset-digest", "value": "games"}}
        teams_source = {"changeSignal": {"kind": "asset-digest", "value": "teams"}}
        previous = {
            "manifestVersion": 1,
            "source": {"games": games_source, "teams": teams_source},
            "seasons": {},
        }
        args = argparse.Namespace(output_dir="unused", manifest_url="https://example.test/manifest.json")

        with tempfile.TemporaryDirectory() as output_dir:
            args.output_dir = output_dir
            with (
                patch("scripts.update_data.read_previous_manifest", return_value=previous),
                patch("scripts.update_data.source_release", side_effect=[games_source, teams_source]),
                patch("scripts.update_data.download_source") as download_source,
                patch("scripts.update_data.write_summary"),
                patch("scripts.update_data.write_github_outputs") as write_outputs,
            ):
                deployed = publish(args)

        self.assertFalse(deployed)
        download_source.assert_not_called()
        write_outputs.assert_called_once_with(False)

    def test_workflow_token_is_only_sent_to_github_api(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b"{}"

        with patch.dict(os.environ, {"GITHUB_TOKEN": "workflow-token"}):
            with patch("scripts.update_data.urllib.request.urlopen", return_value=response) as urlopen:
                request_bytes("https://api.github.com/repos/nflverse/nflverse-data/releases")
                api_request = urlopen.call_args.args[0]
                request_bytes("https://github.com/nflverse/nflverse-data/releases/download/schedules/games.csv")
                source_request = urlopen.call_args.args[0]

        self.assertEqual(api_request.get_header("Authorization"), "Bearer workflow-token")
        self.assertIsNone(source_request.get_header("Authorization"))


if __name__ == "__main__":
    unittest.main()
