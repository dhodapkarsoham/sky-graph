# Data sources, licenses and attribution

`sky.rl` and everything in `data/processed/` are derived from the three
datasets below. Exact URLs, retrieval times and SHA-256 hashes for the copies
used are in [`data/raw/MANIFEST.json`](data/raw/MANIFEST.json).

## 1. HYG star database v4.4 (stars)

- **Author:** David Nash (astronexus). Compiled from Hipparcos, Yale Bright
  Star, and Gliese catalogs, with Gaia DR3 updates.
- **Source:** https://codeberg.org/astronexus/hyg (file
  `data/hyg/CURRENT/hyg_v44.csv.gz`). The project moved from GitHub
  (`astronexus/HYG-Database`) to Codeberg.
- **License:** [Creative Commons Attribution-ShareAlike 4.0 International (CC BY-SA 4.0)](https://creativecommons.org/licenses/by-sa/4.0/).
- **Used for:** `Star` nodes (naked-eye stars with V ≤ 6.5, all stars within
  100 ly, and exoplanet hosts it contains), positions, distances, magnitudes,
  spectral types, constellation membership, and `NEAR` distances.
- **⚠ ShareAlike:** `sky.rl`, `data/processed/stars.json`, `near.json` and
  `line_to.json` are adaptations of HYG. **If you publish them (for example,
  as a download behind the blog page), license them under CC BY-SA 4.0** and
  credit HYG. Showing rendered results such as charts, a sky map or query
  output on the page only needs the credit line.
- **Suggested credit:** "Star data: HYG Database v4.4 by David Nash
  (astronexus), CC BY-SA 4.0, codeberg.org/astronexus/hyg."

## 2. NASA Exoplanet Archive: Planetary Systems Composite Parameters (planets)

- **Publisher:** NASA Exoplanet Science Institute (NExScI), Caltech/IPAC.
- **Source:** https://exoplanetarchive.ipac.caltech.edu, table `pscomppars`,
  queried through the TAP service (query in `pipeline/fetch.py`).
- **Table DOI:** [10.26133/NEA13](https://doi.org/10.26133/NEA13).
- **License:** No explicit license is stated. As a NASA-funded public
  archive, the data is freely usable. The archive asks for the
  acknowledgment below and a citation of its paper.
- **Used for:** `Planet` nodes (radius, mass, period, semi-major axis,
  equilibrium temperature, insolation, discovery year), `Mission` (discovery
  facility), `Method` (discovery method), and host stars missing from HYG
  (position, distance, V magnitude, spectral type).
- **Required acknowledgment (verbatim):** "This research has made use of the
  NASA Exoplanet Archive, which is operated by the California Institute of
  Technology, under contract with the National Aeronautics and Space
  Administration under the Exoplanet Exploration Program."
- **Citation:** Christiansen, J. L., et al. 2025, *The NASA Exoplanet
  Archive*, PSJ, [doi:10.3847/PSJ/ade3c2](https://doi.org/10.3847/PSJ/ade3c2)
  (replaces Akeson et al. 2013). Table: NASA Exoplanet Archive, Planetary
  Systems Composite Parameters, doi:10.26133/NEA13, accessed 2026-11-25.
- Composite values in `pscomppars` mix measurements from different papers
  (and some archive-computed values). They are fine for a visual overview
  but are not a self-consistent solution for any single system.

## 3. d3-celestial constellation data (stick figures and names)

- **Author:** Olaf Frohn.
- **Source:** https://github.com/ofrohn/d3-celestial, tag v0.7.32
  (`data/constellations.lines.json`, `data/constellations.json`), fetched
  via jsDelivr.
- **License:** BSD 3-Clause. Copyright (c) 2015, Olaf Frohn. The full text
  is in [`LICENSE-d3-celestial.txt`](LICENSE-d3-celestial.txt).
- **Used for:** `Constellation` nodes (88 IAU constellations; d3-celestial's
  Serpens Caput and Cauda are merged into one Serpens), and `LINE_TO`
  edges. Each stick-figure vertex was snapped to the nearest naked-eye HYG
  star within 0.25°; all 893 vertices matched.
- **Obligation:** if you redistribute the line data or anything derived from
  it (including `sky.rl`), keep the copyright notice and license text with
  it. That is what `LICENSE-d3-celestial.txt` is for.

## Methods and tools (credit, not data licenses)

- **Constellation lookup for hosts missing from HYG:** astropy
  `get_constellation` (BSD 3-Clause), which uses the IAU boundaries from
  Roman, N. G. 1987, PASP 99, 695.
- **Mass–radius imputation** (used only for planets with one of the two
  missing, and only inside the feature vector): Chen, J. & Kipping, D. 2017,
  ApJ 834, 17.
- **Database:** Relate by Neo4j (relatedb.ai), Apache 2.0.

## Ready-to-paste footer for the blog post

> Star data: HYG Database v4.4 by David Nash (astronexus), CC BY-SA 4.0.
> Planet data: NASA Exoplanet Archive, Planetary Systems Composite Parameters
> (doi:10.26133/NEA13). This research has made use of the NASA Exoplanet
> Archive, which is operated by the California Institute of Technology, under
> contract with the National Aeronautics and Space Administration under the
> Exoplanet Exploration Program. Constellation figures: d3-celestial by Olaf
> Frohn, BSD 3-Clause.
