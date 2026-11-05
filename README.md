# Mutual Funds Pipeline — Trade Republic España

Cuatro notebooks, un pipeline común y resultados identificados por **ISIN**.
Acciones, fondos tradicionales y ETF se analizan por separado. El cuarto notebook
resume sus top 5 globales y por sector/categoría con lenguaje sencillo.

## Empezar

Python 3.11 o superior. Desde la raíz, en PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m ipykernel install --user --name tr-funds --display-name "Python (TR Funds)"
.\.venv\Scripts\python.exe -m jupyterlab
```

Selecciona **Python (TR Funds)** y ejecuta los notebooks en este orden:

1. [01_acciones.ipynb](notebooks/01_acciones.ipynb): precios, EDA, medias, PER/EPS y rankings.
2. [02_fondos.ipynb](notebooks/02_fondos.ipynb): fondos tradicionales, categorías y rankings.
3. [03_etfs.ipynb](notebooks/03_etfs.ipynb): ETF, categorías y rankings.
4. [04_conclusiones.ipynb](notebooks/04_conclusiones.ipynb): lectura sencilla de los resultados anteriores.

También puedes actualizar el pipeline y el informe sin abrir Jupyter:

```powershell
.\.venv\Scripts\tr-pipeline.exe run
```

O ejecutar y guardar las salidas de los cuatro notebooks automáticamente:

```powershell
.\.venv\Scripts\tr-pipeline.exe notebooks
```

Este último comando usa el kernel `python3`: ejecútalo con la `.venv` activada
(`.\.venv\Scripts\Activate.ps1`) para que el kernel herede el mismo entorno.
Si la política de PowerShell impide activarla, usa Jupyter con el kernel **Python (TR Funds)**.

**Por defecto se analizan hasta 250 acciones, 250 fondos tradicionales y 250 ETF.**
Primero se procesan fondos y ETF. La selección sigue el **orden predeterminado del
explorador público de TR**, un proxy de relevancia cuya fórmula TR no publica:
no acredita patrimonio, volumen ni calidad y no se eligen los primeros ISIN alfabéticos.
`max_assets_per_type = 250` en `config.toml` controla el máximo. Se consulta solo
la parte inicial del explorador, sin enumerar miles de instrumentos ni descargar
sus históricos para elegir los 250. Los ETF candidatos se validan después como UCITS;
un fallo de datos o validación queda pendiente y no se sustituye silenciosamente por otro activo.

Las fuentes gratuitas pueden limitar peticiones. Las descargas correctas
se guardan por ISIN: al repetir la ejecución se reutilizan durante 24 horas.
No hay suscripciones, claves de pago ni conexión a tu cuenta de Trade Republic.

Para una comprobación rápida con activos concretos del catálogo:

```powershell
.\.venv\Scripts\tr-pipeline.exe run --isin US0378331005 --isin IE00B03HD191 --isin IE00B4L5Y983
```

El resultado de esa selección se etiqueta **parcial**. No se presenta como un top
de todo Trade Republic. `run --limit 5` prueba los primeros cinco **por relevancia**
de cada tipo y nunca amplía el máximo configurado. La selección predeterminada de
250 es el alcance normal del proyecto; los top 5 por rentabilidad se calculan dentro
de ella. Se guardan el orden y la fuente en `data/processed/<tipo>/selection.csv`.

## Universo y fuentes

La fuente principal es el [explorador público de Trade Republic España](https://app.traderepublic.com/browse/mutualFund?jurisdiction=ES).
Se consultan acciones (`stock`), fondos (`mutualFund`) y candidatos a ETF (`fund`),
con filtro de jurisdicción `ES`. Se conserva el nombre, el ISIN, la gestora,
las etiquetas de sector, la fuente y la fecha de consulta.

El catálogo se descarga a través del mismo servicio público que usa ese explorador,
**sin autenticación**. Su protocolo no es una API oficial estable. Si cambia, el
proveedor falla explícitamente; no introduce activos de otra fuente silenciosamente.
Se mantiene el orden global de la búsqueda vacía, se colapsan repeticiones entre
páginas por primera aparición y se guarda `relevance_rank` numérico por tipo.
No se particiona por gestora ni país para elegir la selección, pues alteraría ese orden.
Se contrastan los seleccionados con el total publicado y se detectan cambios de conteo.
El manifest distingue catálogo completo y selección recuperada. Se valida el checksum de los ISIN.

Las gestoras aparecen en función de su posición dentro de los 250, sin garantizar
cuotas por gestora. Una gestora se identifica también por la clase concreta del fondo;
no se sustituyen clases de acumulación, distribución o divisa entre sí.

Antes de descargar un activo del explorador se comprueba que TR lo declare activo
en España. En ETF se exige además `ucits=true`, porque el apartado `fund` puede
mezclar ETC/ETN y otros vehículos. Los que no se acreditan quedan en `failures.csv`;
**el ranking de esta versión es de ETF UCITS verificables**, no de todos los ETP.
La oferta pública puede diferir de restricciones de una cuenta concreta.

Fuentes gratuitas utilizadas:

| Fuente | Uso | Límite práctico |
| --- | --- | --- |
| Trade Republic público | ISIN, disponibilidad ES, nombre, sector/categoría y tipo | Protocolo sin garantía de estabilidad; catálogo cambiante |
| Yahoo Finance mediante `yfinance` | Cierres ajustados, eventos corporativos, EPS, PER y otros fundamentales | Cobertura irregular, posibles errores y restricciones de uso del proveedor |
| BCE mediante Frankfurter v1 | Conversión diaria de monedas a EUR | Tipos de referencia, no cambio efectivo cobrado por el bróker |
| Informes y CSV del emisor | Alternativa para una clase sin precio o EPS fiable en Yahoo | Hay que aportar el CSV con URL de procedencia |

El registro [CNMV de Trade Republic como comercializadora](https://www.cnmv.es/portal/consultas/iic/comercializadora?lang=es&nif=W0322893I)
sirve como contraste de las familias comercializadas. **No se interpreta como una
lista de todas las clases/ISIN disponibles.** Consulta los enlaces técnicos y la
fecha de comprobación en [docs/fuentes.md](docs/fuentes.md).

Se busca Yahoo por **ISIN exacto y sin fuzzy**. Se comprueba la correspondencia
inversa cuando Yahoo la proporciona; si no, se acepta un único resultado del tipo
esperado y se registra el método. Una respuesta ambigua, un ISIN inverso distinto
o un tipo incompatible quedan pendientes; nunca se elige por nombre aproximado.
Yahoo etiqueta algunas cotizaciones de fondos tradicionales como `ETF`. Se admite
esa etiqueta al resolver el ISIN de un fondo del catálogo, manteniendo su tipo
`mutual_fund` y registrando la discrepancia. Ese cierre bursátil de la misma clase
no se presenta como NAV oficial; las clases de distribución siguen necesitando
distribuciones o ajuste verificable para entrar en el ranking.
Para resolver un caso revisado, añade su ticker y la URL que acredita la misma
clase a `data/manual/mappings.csv`.

## Limpieza y análisis

- Un registro por ISIN y fecha: se conserva el último duplicado y se contabilizan
  los conflictos. Un conflicto excluye el activo del ranking hasta revisarlo.
- Precios no positivos/no finitos se tratan como ausentes. Se eliminan fechas inválidas.
- Calendario de lunes a viernes. Los huecos, incluidos festivos, toman el último
  precio anterior (`ffill`), sin fines de semana, sin rellenar hacia atrás y sin
  extender el precio después del último cierre/NAV observado.
- Se guardan `price`, `adjusted_price`, `log_return` y `was_imputed`, además de
  la fecha y el ISIN. El retorno es `ln(P_ajustado[t] / P_ajustado[t-1])`.
- `Adj Close` de Yahoo incorpora los ajustes por dividendos y splits. Se conservan
  los eventos de split y se pide `repair=True`; no se ajusta dos veces. Esto es
  corrección de desdoblamientos, no «desdoblar» filas duplicadas.
- EDA: descriptivos de retornos, distribución, volatilidad, caída máxima,
  duplicados, missing, fracción imputada y evolución de los líderes.
- Medias de **3, 6 y 12 meses naturales** y diferencia porcentual del precio actual
  frente a cada media, usando la misma serie ajustada en EUR. Se necesita la
  ventana completa; una serie nueva no aparenta tener un año de historia.
- Top 5 global y por sector/categoría de fondos y ETF: **rentabilidad histórica anualizada
  (CAGR) en EUR**, de mayor a menor. La columna inmediatamente a su derecha muestra el
  **retorno de los últimos 12 meses**. Se exportan también la fecha inicial, la final
  y los años de histórico. `CAGR = ((precio ajustado EUR final / inicial)^(1/años) - 1) × 100`,
  con años = días naturales / 365,25 y extremos observados. Se usa todo el histórico descargado
  de cada ISIN desde la fecha configurada, no necesariamente toda su vida; los periodos
  pueden diferir. Los filtros existentes de calidad y disponibilidad de un año se mantienen;
  los huecos del histórico completo se auditan en `quality.csv` y en EDA.
- Las acciones mantienen su ranking por **retorno total acumulado de 12 meses en EUR**.
  Se publican también retornos de 3 y 6 meses. No se mezclan divisas ni tipos de activos.
- Referencia: día anterior a la ejecución, para evitar velas intradía sin cerrar.
  Cada activo conserva la fecha de su último precio real. Por defecto se excluyen
  series con más de 5 días laborables de retraso o más del 5% imputado en el año.
  La fecha común de referencia puede diferir unos días del último NAV publicado.
- Las clases de distribución sin eventos de reparto ni ajuste verificable quedan
  fuera del ranking: comparar solo NAV/cierre sin dividendos penalizaría esas clases.
- Se usan sectores y categorías publicados. Los fondos diversificados conservan
  categorías como renta variable global o bonos. Solo los activos sin clasificar
  pueden pasar a clustering (mínimo 4 activos y 60 días comunes): los grupos se
  etiquetan como estadísticos, sin inventar un sector económico. Puede desactivarse.

Los retornos son antes de impuestos, comisiones de compra/venta y spreads. El NAV
de un fondo refleja sus gastos internos, pero no los costes particulares de tu cuenta.
Un top global puede mezclar productos de riesgos muy distintos. Es EDA descriptivo
del universo actual: no es un backtest histórico libre de sesgo de supervivencia.

### Acciones: PER histórico frente al actual

Para cada año fiscal disponible: `PER anual = precio medio del año / EPS diluido anual`.
Se compara la mediana de **al menos tres años válidos** con el PER TTM actual
publicado por Yahoo. Se muestran EPS TTM/forward, PER forward, crecimiento de
beneficios/ingresos, ROE, margen y deuda cuando existen.

Es un **proxy retrospectivo anual**, no la reconstrucción de los PER TTM que el
mercado conocía en cada fecha. Yahoo puede reformular los estados financieros.
No se multiplica un PER por 252 ni se considera el PER forward como un dato realizado.
EPS negativos/cero, distintas divisas o splits sin una base de EPS comprobable
impiden la comparación. Los EPS ajustados del emisor pueden aportarse por CSV.

Reglas sencillas, visibles en el código: PER al menos un 20% inferior a su mediana
→ «revisar posible value»; al menos un 20% superior y beneficio creciente
→ «revisar growth». No son recomendaciones de compra ni una valoración completa.
Los top 5 de acciones se ordenan por retorno de los últimos 12 meses; estas señales
de valoración aparecen en una tabla adicional sin mezclarse con ese ranking.

## Actualización y archivos

```text
config.toml                    Fechas, calidad, caché, clustering y país
src/tr_pipeline/               Proveedores, limpieza, análisis e informes
notebooks/01..04               Tres análisis y sus conclusiones
data/catalog/universe.csv      Catálogo actualizado, por ISIN
data/catalog/manifest.json     Fuente, conteos, hash y cobertura
data/raw/                      Descargas originales y caché de divisas
data/manual/                   Fuentes y correspondencias revisadas
data/processed/<tipo>/         prices, metrics, quality, failures, pe_annual, manifest
reports/                       Top 5 CSV, conclusiones.md y dependencias
tests/                         Cálculos y ejecución integral de los notebooks
```

`04_conclusiones` consume los CSV generados por los tres análisis y comprueba una
firma de fecha, código, configuración, catálogo y entradas manuales. Si cambia
cualquiera, reconstruye las dependencias. Un ensayo con `--limit`/`--isin` nunca
se reutiliza como si fuese un análisis completo. Abrir 04 después de un ensayo
provoca una ejecución normal de la selección configurada de hasta 250 por tipo.
Una selección normal ya calculada se reutiliza aunque no cubra todo el catálogo TR.

Los precios se guardan tanto en `prices.csv` como en `prices/<ISIN>.csv`. La escritura
es progresiva y las figuras de los líderes leen solo sus series. El histograma de
retornos usa una muestra por bloques; sus valores no intervienen en los rankings.

```powershell
tr-pipeline catalog --refresh        # Solo actualizar el universo
tr-pipeline run --type mutual_fund   # Solo fondos
tr-pipeline run --refresh            # Actualizar selección e históricos (máximo 250 por tipo)
tr-pipeline run --offline            # Usar cachés/CSV; auditar lo que falta
tr-pipeline conclusions             # Actualizar conclusiones y dependencias pendientes
python -m pytest -q                 # Pruebas sin red; datos sintéticos aislados
```

También funciona `python scripts/run_pipeline.py <comando>` sin el comando instalado.
Para una actualización diaria puedes programar `tr-pipeline run` en el Programador
de tareas de Windows, con la raíz del repositorio como directorio de trabajo.

### Entradas manuales y catálogo acreditado

`data/manual/universe.csv` permite añadir/actualizar ISIN con columnas
`isin,name,asset_type,source,verified_at`, donde los tipos son `stock`, `mutual_fund`
y `etf`. Una aportación manual no se considera prueba de un catálogo completo.
No añadas todos los productos de una gestora suponiendo que TR los distribuye.

`data/manual/mappings.csv`: `isin,yahoo_symbol,identity_source,sector,sector_source`.
Las URLs de identidad y de clasificación deben acreditar la misma clase/activo.
Se incluyen correspondencias revisadas para Alphabet clase A y Fidelity MSCI World
P EUR Acc, que Yahoo no resuelve de forma única mediante su buscador por ISIN.
Para precios y EPS de emisores hay instrucciones junto a los respectivos directorios.

Si obtienes un listado completo de tu cuenta, configura `authoritative_catalog`
con su ruta. Acompáñalo de un JSON del mismo nombre con:

```json
{"complete": true, "country": "ES", "as_of": "AAAA-MM-DD", "source": "procedencia verificable"}
```

La fecha debe ser válida, no futura y de los últimos siete días por defecto.
`require_complete_universe=true` impide continuar si no se acredita la cobertura.
Si un catálogo aportado supera el máximo por tipo necesita una columna
`relevance_rank` positiva y numérica (menor = más relevante); el pipeline no inventa
relevancia a partir del ISIN. Para volver a enumerar todo el universo público,
elimina `max_assets_per_type` de la configuración: esa operación puede tardar mucho.
Para usar el PDF oficial como alternativa establece `catalog_source="pdf"`;
ese catálogo se marca parcial porque no incluye fondos tradicionales.

El proyecto no distribuye datos financieros con licencia del proveedor:
descargas y resultados quedan fuera de Git. Los cuatro notebooks se versionan
sin salidas; las pruebas ejecutan esos mismos archivos con datos sintéticos en
un directorio temporal, separado de tus resultados reales.

### Datos alternativos y Hugging Face

Se revisaron datasets el 5 de noviembre de 2026; el resultado y las limitaciones
están en [docs/fuentes.md](docs/fuentes.md). No se incorporan series de otros países
o clases como sustitutos del mismo fondo. Para cubrir un ISIN pendiente puedes
aportar `data/manual/prices/<ISIN>.csv` con
`date,price,adjusted_price,currency,source_url`, también si procede de un dataset.
Debe acreditar la clase exacta, la divisa y el tratamiento de distribuciones.
Consulta [el formato de precios](data/manual/prices/README.md). Estas entradas
tienen prioridad sobre Yahoo y funcionan con `--offline` si el catálogo y los
metadatos TR necesarios ya están en caché.
