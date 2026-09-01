"""Render a styled PNG table of the top 50 counties by brewery density.

Population-floored (>=50k adults 21+) and ranked by the project's adopted
headline model (covariates + state FE + BYM2 spatial random effect, see
fit_combined_spatial_covariate_model.py and methods memo Section 15), not
the raw rate, for the same reason the choropleth has a floored variant —
raw rates are dominated by small-county noise.
"""

from __future__ import annotations

import matplotlib.pyplot as plt
import pandas as pd

POPULATION_FLOOR = 50_000
TOP_N = 50


def main() -> None:
    df = pd.read_parquet("data/processed/us_county_combined_model_rankings.parquet")
    top = (
        df[df["adults_21plus"] >= POPULATION_FLOOR]
        .sort_values("combined_posterior_rate_per_100k", ascending=False)
        .head(TOP_N)
        .reset_index(drop=True)
    )

    rows = []
    for i, r in top.iterrows():
        rows.append([
            str(i + 1),
            f"{r['county_name']}, {r['state_abbr']}",
            f"{r['obdb_count']:.0f}",
            f"{r['adults_21plus']:,.0f}",
            f"{r['obdb_rate_per_100k_21plus']:.1f}",
            f"{r['combined_posterior_rate_per_100k']:.1f}",
        ])

    col_labels = ["Rank", "County", "Breweries", "Adults 21+", "Raw rate\n/100k", "Model rate\n/100k"]
    col_widths = [0.06, 0.34, 0.13, 0.15, 0.15, 0.17]

    n_rows = len(rows) + 1
    fig_height = 0.32 * n_rows + 1.2
    fig, ax = plt.subplots(figsize=(10, fig_height))
    ax.axis("off")

    table = ax.table(cellText=rows, colLabels=col_labels, colWidths=col_widths,
                      cellLoc="center", loc="center")
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    table.scale(1, 1.55)

    header_color = "#8a4008"
    stripe_color = "#fff5e6"
    for (row, col), cell in table.get_celld().items():
        cell.set_edgecolor("#dddddd")
        if row == 0:
            cell.set_text_props(weight="bold", color="white")
            cell.set_facecolor(header_color)
        else:
            cell.set_facecolor(stripe_color if row % 2 == 0 else "white")
            if col == 1:
                cell.set_text_props(ha="left")
                cell.PAD = 0.02

    fig.suptitle(f"Top {TOP_N} US Counties by Brewery Density", fontsize=16, fontweight="bold", y=0.99)
    fig.text(0.5, 0.955,
              f"Population floor: >= {POPULATION_FLOOR:,} adults 21+. Ranked by the project's "
              "adopted model (covariates + state FE + spatial random effect), not raw rate "
              "(see methods memo Section 15).",
              ha="center", fontsize=9, color="#555555")
    fig.text(0.5, 0.01,
              "Source: Open Brewery DB (uncorrected for coverage gap, estimated 7-38% "
              "undercount depending on state) + Census ACS 5-year (2020-2024).",
              ha="center", fontsize=7.5, color="#777777")

    out_path = "data/processed/us_top50_county_brewery_density_table.png"
    fig.savefig(out_path, dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
