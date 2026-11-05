"""Regenera los cuatro notebooks (sin resultados guardados)."""
from pathlib import Path

import nbformat as nbf

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = '''from pathlib import Path
import os
import sys

ROOT = Path(os.environ["TR_PIPELINE_ROOT"]).resolve() if os.getenv("TR_PIPELINE_ROOT") else Path.cwd()
if not (ROOT / "config.toml").exists():
    ROOT = ROOT.parent
sys.path.insert(0, str(ROOT / "src"))
from tr_pipeline.pipeline import run_asset_type, conclusions
from tr_pipeline.notebook import show_coverage, show_eda, show_rankings
from IPython.display import display, Markdown
import pandas as pd

REFRESH = False  # True fuerza una descarga nueva de Yahoo y divisas.
OFFLINE = os.getenv("TR_PIPELINE_OFFLINE", "0") == "1"
'''


def save(filename, cells):
    nb = nbf.v4.new_notebook(cells=cells)
    nb.metadata = {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                   "language_info": {"name": "python", "version": "3.11"}}
    for i, cell in enumerate(nb.cells):
        cell["id"] = f"cell-{i:02d}"
    nbf.write(nb, ROOT / "notebooks" / filename)


for filename, kind, title in [
    ("01_acciones.ipynb", "stock", "Acciones individuales"),
    ("02_fondos.ipynb", "mutual_fund", "Fondos de inversión tradicionales (no ETF)"),
    ("03_etfs.ipynb", "etf", "ETF"),
]:
    intro = f'''# {title}

Ejecuta todas las celdas. Este notebook descarga/carga, limpia y actualiza los CSV
que alimentan `04_conclusiones.ipynb`. Configuración común en `config.toml`.

**El ISIN es la identidad del activo.** Solo se estudian ISIN del catálogo cargado de
Trade Republic. Si el catálogo o la cobertura de precios son parciales se indica expresamente.
Por defecto se seleccionan **hasta 250 por tipo**, usando el orden predeterminado del
explorador TR como proxy de relevancia (fórmula no publicada), no el orden alfabético del ISIN.
La selección se guarda en `data/processed/{kind}/selection.csv`. El top por rentabilidad
se calcula después sobre esta selección. El máximo común está en `config.toml`.

Precio: último cierre o valor liquidativo, no cotización intradía. Se conserva el cierre
y su precio ajustado por dividendos/distribuciones y splits. Retorno logarítmico:
`ln(precio_ajustado_hoy / precio_ajustado_anterior)`.

Se elimina el duplicado de fecha conservando la última fila y se auditan los conflictos.
Se rellenan huecos de lunes a viernes con el dato anterior, incluidos festivos, sin añadir fines de
semana ni extender un activo más allá de su último dato. No se rellena hacia atrás.
Un exceso de huecos o un precio antiguo excluyen el activo del ranking.
'''
    cells = [nbf.v4.new_markdown_cell(intro), nbf.v4.new_code_cell(BOOTSTRAP),
             nbf.v4.new_markdown_cell("## 1. Pipeline y cobertura\n\nUna correspondencia dudosa entre ISIN y ticker queda pendiente para revisión."),
             nbf.v4.new_code_cell(f'RESULT = run_asset_type(ROOT, "{kind}", refresh=REFRESH, offline=OFFLINE)\nshow_coverage(RESULT)'),
             nbf.v4.new_markdown_cell("## 2. Limpieza, EDA y medias\n\nMedias de los últimos 3, 6 y 12 meses naturales. Comparación en EUR, usando la misma serie ajustada. "
                                      "Los huecos imputados se conservan con una marca para auditar su efecto."),
             nbf.v4.new_code_cell(f'show_eda(ROOT, "{kind}", RESULT)')]
    if kind == "stock":
        cells += [nbf.v4.new_markdown_cell('''## 3. PER y EPS: posibles candidatos para estudiar

PER histórico anual = **precio medio del año fiscal / EPS diluido anual positivo**.
Se compara la mediana de al menos tres años con el PER TTM actual publicado por Yahoo.
Es una comparación retrospectiva aproximada, no una serie de PER TTM históricos ni un backtest.
Los informes pueden estar reformulados. No se utiliza PER forward como si fuera realizado.

Solo se compara con EPS en la misma divisa y base de acciones. Si hay splits y esa base
no se puede comprobar, se necesitan los EPS ajustados de los informes del emisor.
PER un 20% inferior a su mediana: candidato a revisar por value.
PER un 20% superior y crecimiento positivo de beneficios: candidato a revisar por growth.
La deuda, márgenes, ROE, crecimiento de ingresos y EPS esperado se muestran cuando están publicados.
Son reglas exploratorias; no valoran por sí solas el negocio ni predicen su rendimiento.
'''), nbf.v4.new_code_cell('''metrics = RESULT["metrics"]
if not metrics.empty:
    display(metrics[["isin", "name", "pe_current", "pe_forward", "eps_ttm", "eps_forward",
                     "pe_historical_median", "pe_discount_pct", "earnings_growth", "revenue_growth",
                     "roe", "debt_to_equity", "profit_margin", "valuation_signal", "valuation_reason"]])
    display(pd.read_csv(ROOT / "data/processed/stock/pe_annual.csv"))
else:
    display(Markdown("No hay comparaciones de PER disponibles."))''')]
    else:
        cells += [nbf.v4.new_markdown_cell('''## 3. Qué significa «sector» en un fondo

Se usa la categoría publicada del fondo: renta variable global, bonos, tecnología, etc.
Un fondo diversificado no se fuerza a pertenecer a un solo sector corporativo.
Solo si falta esa clasificación se agrupan trayectorias mediante clustering (mínimo
4 activos y 60 observaciones comunes). El resultado se llama «grupo estadístico»,
sin atribuirle un sector económico inventado. Las categorías conocidas se conservan.
''')]
    ranking_text = '''## 4. Top 5 global y por sector/categoría

Orden: rentabilidad total de los últimos **12 meses en EUR**, con idéntica fecha de referencia.
Si hay menos de cinco activos elegibles se muestran los que existan. Acciones, fondos y ETF
se clasifican por separado. Los activos sin datos suficientes siguen visibles en el informe de cobertura.
'''
    if kind != "stock":
        ranking_text = '''## 4. Top 5 global y por sector/categoría

Orden: **rentabilidad histórica anualizada (CAGR) en EUR**, de mayor a menor.
Justo a la derecha del CAGR se muestra la **rentabilidad de los últimos 12 meses**.
El criterio se aplica tanto al top global como al top por sector/categoría.

`CAGR = ((precio ajustado EUR final / inicial) ** (1 / años) - 1) * 100`

Se usa todo el histórico descargado desde el primer precio observado de cada ISIN
hasta el último, con años = días naturales / 365,25. Es una media compuesta, no una
media aritmética. Se muestran las fechas y los años porque los periodos pueden ser distintos;
el inicio configurado limita el histórico y no equivale necesariamente a toda la vida del fondo.

Se conservan los filtros existentes: un año completo, datos recientes y calidad del último año.
Los huecos del histórico completo se muestran en la tabla de limpieza; el CAGR usa los extremos
observados, sin extrapolar una fecha inicial anterior. Los activos excluidos siguen visibles en EDA.
Si hay menos de cinco activos elegibles se muestran los disponibles. Fondos y ETF se clasifican por separado.
'''
    cells += [nbf.v4.new_markdown_cell(ranking_text), nbf.v4.new_code_cell('show_rankings(RESULT)')]
    save(filename, cells)

save("04_conclusiones.ipynb", [
    nbf.v4.new_markdown_cell('''# Conclusiones: qué ha rendido más

Este notebook lee los resultados de los otros tres análisis. Si faltan o cambia
la fecha, el catálogo, el código, la configuración o un CSV manual, recalcula el pipeline necesario.
También puedes actualizarlo todo con `tr-pipeline notebooks`.

**Lectura rápida:** en fondos y ETF el puesto 1 tiene la mayor **rentabilidad histórica
anualizada (CAGR) en EUR**. La columna inmediatamente a su derecha muestra la rentabilidad
de los **últimos 12 meses**. Las fechas inicial/final y los años indican el periodo disponible;
puede variar entre activos y no equivale necesariamente a toda su vida.
Se conservan los filtros de calidad del último año y se auditan también los huecos del histórico completo.
Las acciones mantienen su ranking por rentabilidad de los últimos 12 meses.
Es una clasificación de rendimiento pasado. No indica qué comprar.
Un informe parcial se identifica antes de mostrar sus rankings.
'''),
    nbf.v4.new_code_cell(BOOTSTRAP),
    nbf.v4.new_code_cell('RESULTS = conclusions(ROOT, refresh_dependencies=True, offline=OFFLINE)'),
    nbf.v4.new_code_cell('''for kind, result in RESULTS.items():
    show_coverage(result)
    show_rankings(result)
    if kind == "stock":
        metrics = pd.read_csv(ROOT / "data/processed/stock/metrics.csv")
        if "pe_discount_pct" in metrics:
            candidates = metrics.loc[metrics.pe_discount_pct.notna()].sort_values("pe_discount_pct", ascending=False).head(5)
            display(Markdown("### PER: candidatos para revisar"))
            if len(candidates):
                display(candidates[["isin", "name", "pe_current", "pe_historical_median", "valuation_signal"]])
            else:
                display(Markdown("No hay comparaciones históricas suficientes y fiables."))'''),
    nbf.v4.new_markdown_cell('''Se ha guardado la misma lectura en `reports/conclusiones.md`, junto con
los CSV de top 5 y un manifest que identifica los análisis de origen.
Consulta `data/processed/<tipo>/failures.csv` para ver los ISIN pendientes de datos.
'''),
])
print("Generados cuatro notebooks.")

