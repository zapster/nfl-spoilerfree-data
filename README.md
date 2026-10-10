# NFL Spoiler-Free Data

Public, static, season-scoped snapshots of the nflverse game schedule and team metadata. The site is published with GitHub Pages from GitHub Actions; no generated CSV data is committed to this repository.

## Data

The current files and their SHA-256 hashes are listed in [`manifest.json`](https://zapster.github.io/nfl-spoilerfree-data/manifest.json). Each season entry points to a source-schema CSV under `seasons/`. The manifest also records nflverse release signals, update timestamps, record totals, and game-type counts. Team metadata is filtered to teams referenced by the published seasons.

### Attribution and License

The upstream [`nflverse-data`](https://github.com/nflverse/nflverse-data) repository declares its material under [Creative Commons Attribution 4.0 International (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/). The published datasets are derived from its `games.csv` and `teams_colors_logos.csv` releases. Attribution: **nflverse-data contributors**, [nflverse-data](https://github.com/nflverse/nflverse-data), licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

Changes made here: game rows are filtered and grouped into per-season files, and team rows are filtered to teams used by the published seasons. The source CSV columns and values are retained. The data and manifest provide this attribution and license information for downstream users.

CC BY 4.0 does not grant trademark rights or rights that nflverse does not hold. nflverse states that NFL data belong to their respective owners and are governed by those owners' terms. This repository is an independent, unofficial redistribution and does not include the logo images themselves; the team metadata CSV retains source-provided logo URL fields. See [`DATA-LICENSE.md`](DATA-LICENSE.md).

Season files are discovered from the upstream source, so historical seasons and newly added seasons are published without changing the workflow. A change limited to another season does not alter the hash-versioned file for an unchanged season.

## Updating

The workflow checks the independent nflverse `schedules` and `teams` releases hourly at minute 17, and can also be run manually from the Actions tab. It reads small GitHub release metadata on each run and downloads a source CSV only when that source's release signal changes. It validates generated data before Pages deployment and skips deployment when sources are unchanged.

GitHub may disable scheduled workflows after 60 days without repository activity. Check the Actions tab periodically and manually re-enable the schedule if necessary.

To run the updater locally:

```sh
python3 scripts/update_data.py --output-dir _site
```

The command uses the currently deployed manifest as its previous snapshot. On initial publication, when no manifest exists yet, it downloads both public nflverse CSV files.
