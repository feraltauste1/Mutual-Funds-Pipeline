"""Presentación compartida: los notebooks mantienen el análisis visible y sencillo."""
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
from IPython.display import Markdown, display

from .common import LABELS
from .pipeline import table_markdown


def show_coverage(result: dict):
    m = result["manifest"]
    display(Markdown(f"### {LABELS[m['asset_type']]}\n"
                     f"Referencia: **{m['as_of']}**. Catálogo cargado: **{m['catalog_total']}** ISIN. "
                     f"Con precios: **{m['downloaded']}**. Elegibles: **{m['eligible']}**. "
                     f"Pendientes/error: **{m['failed']}**."))
    display(Markdown(f"Selección: **{m['attempted']}** de **{m.get('published_total', m['catalog_total'])}** publicados. "
                     f"Criterio: {m.get('selection_method', 'Ver catálogo')}. "
                     "El ranking por rentabilidad se calcula después sobre esta selección."))
    if not m["complete_analysis"]:
        display(Markdown("**RESULTADOS PARCIALES: no son el top de todo Trade Republic.**\n\n" + m["catalog_note"]))


def show_rankings(result: dict):
    display(Markdown("### Top 5 global por rentabilidad a un año en EUR\n\n" + table_markdown(result["top_global"])))
    display(Markdown("### Top 5 por sector o categoría"))
    for sector, rows in result["top_sector"].groupby("sector", sort=True):
        display(Markdown(f"**{sector}**\n\n" + table_markdown(rows)))
    if result["top_sector"].empty:
        display(Markdown("Sin activos elegibles para clasificar."))


def show_eda(root: Path, kind: str, result: dict):
    folder = root / f"data/processed/{kind}"
    quality = pd.read_csv(folder / "quality.csv")
    failures = pd.read_csv(folder / "failures.csv")
    display(Markdown("### Limpieza: duplicados, huecos y splits"))
    display(quality)
    if not failures.empty:
        display(Markdown("### ISIN pendientes de fuente o correspondencia"))
        display(failures)
    metrics = result["metrics"]
    if metrics.empty:
        display(Markdown("No hay precios verificables en esta ejecución. Consulta failures.csv y las fuentes manuales."))
        return
    display(Markdown("### Estadística descriptiva"))
    columns = ["return_3m_pct", "return_6m_pct", "return_12m_pct", "daily_log_return_mean",
               "annualized_volatility_pct", "max_drawdown_pct"]
    display(metrics[columns].describe().T)
    display(Markdown("### Medias de 3, 6 y 12 meses frente al último precio ajustado en EUR\n"
                     "`vs_mean_*_pct`: cuánto está el precio por encima (+) o por debajo (-) de esa media."))
    display(metrics[["isin", "name", "adjusted_price_eur", "mean_3m", "vs_mean_3m_pct", "mean_6m",
                     "vs_mean_6m_pct", "mean_12m", "vs_mean_12m_pct", "last_date"]])
    best = result["top_global"]["isin"].tolist()
    if best:
        prices = pd.concat([pd.read_csv(folder / f"prices/{isin}.csv", parse_dates=["date"]) for isin in best])
        pivot = prices.pivot(index="date", columns="isin", values="adjusted_price_eur")
        pivot = pivot.loc[pivot.index >= pivot.index.max() - pd.DateOffset(years=1)]
        normalized = pivot.divide(pivot.bfill().iloc[0]).mul(100)
        ax = normalized.plot(figsize=(11, 4), title="Top global: evolución en EUR (inicio = 100)")
        ax.set_ylabel("Valor ajustado, base 100")
        ax.set_xlabel("Fecha")
        ax.grid(alpha=.2)
        plt.tight_layout()
        plt.show()
    # Muestra por bloques para no cargar años de miles de activos en memoria.
    samples = []
    for chunk in pd.read_csv(folder / "prices.csv", usecols=["log_return_eur"], chunksize=100_000):
        observed = chunk.log_return_eur.dropna()
        if len(observed):
            samples.append(observed.sample(n=min(1000, len(observed)), random_state=42))
    values = pd.concat(samples, ignore_index=True) if samples else pd.Series(dtype=float)
    if not values.empty:
        _, ax = plt.subplots(figsize=(8, 3))
        ax.hist(values.clip(values.quantile(.005), values.quantile(.995)), bins=60)
        ax.set_title("Muestra de retornos diarios EUR (recortada a percentiles 0,5–99,5)")
        ax.set_xlabel("Retorno logarítmico")
        plt.tight_layout()
        plt.show()

