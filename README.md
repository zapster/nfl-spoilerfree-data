# NFL Spoiler-Free Data

Public, static, season-scoped snapshots of the nflverse game schedule and team metadata. The site is published with GitHub Pages from GitHub Actions; no generated CSV data is committed to this repository.

## Data

The current files and their SHA-256 hashes are listed in [`manifest.json`](https://zapster.github.io/nfl-spoilerfree-data/manifest.json). Each season entry points to a source-schema CSV under `seasons/`. The manifest also records nflverse release signals, update timestamps, record totals, and game-type counts. Team metadata is filtered to teams referenced by the published seasons.

Season files are discovered from the upstream source, so historical seasons and newly added seasons are published without changing the workflow. A change limited to another season does not alter the hash-versioned file for an unchanged season.

## Updating

The workflow checks the independent nflverse `schedules` and `teams` releases hourly at minute 17, and can also be run manually from the Actions tab. It reads small GitHub release metadata on each run and downloads a source CSV only when that source's release signal changes. It validates generated data before Pages deployment and skips deployment when sources are unchanged.

GitHub may disable scheduled workflows after 60 days without repository activity. Check the Actions tab periodically and manually re-enable the schedule if necessary.

To run the updater locally:

```sh
python3 scripts/update_data.py --output-dir _site
```

The command uses the currently deployed manifest as its previous snapshot. On initial publication, when no manifest exists yet, it downloads both public nflverse CSV files.
