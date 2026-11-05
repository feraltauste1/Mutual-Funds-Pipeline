# Fuentes comprobadas el 5 de noviembre de 2026

| Fuente primaria | Aplicación |
| --- | --- |
| [Explorador público: acciones](https://app.traderepublic.com/browse/stock?jurisdiction=ES) | Universo de acciones de TR para España |
| [Explorador público: fondos](https://app.traderepublic.com/browse/mutualFund?jurisdiction=ES) | ISIN de clases de fondos y gestoras |
| [Explorador público: cotizados](https://app.traderepublic.com/browse/fund?jurisdiction=ES) | Candidatos a ETF; se comprueba disponibilidad y UCITS |
| [PDF oficial de TR España](https://assets.traderepublic.com/assets/files/ES/Instrument_Universe_ES_en.pdf) | Alternativa parcial: Stocks y ETF; no lista fondos tradicionales |
| [CNMV: comercializadora TR](https://www.cnmv.es/portal/consultas/iic/comercializadora?lang=es&nif=W0322893I) | Contraste de familias, sin inferir todas las clases |
| [yfinance: precios](https://ranaroussi.github.io/yfinance/reference/yfinance.price_history.html) | Históricos, ajuste de precios y reparación |
| [yfinance: búsqueda](https://ranaroussi.github.io/yfinance/reference/api/yfinance.Search.html) | Consulta por ISIN sin búsqueda aproximada |
| [yfinance: estados de resultados](https://ranaroussi.github.io/yfinance/reference/api/yfinance.Ticker.get_income_stmt.html) | EPS anual diluido |
| [Frankfurter v1](https://frankfurter.dev/v1/) | Series de cambio del BCE, gratuitas y sin clave |

El servicio de catálogo (`wss://api.traderepublic.com`, protocolo 34, temas
`neonSearch`, `neonSearchAggregations`, `instrument`, `mutualFundDetails`) se observó
en los módulos JavaScript publicados por el propio explorador. No es una API
documentada ni respaldada para terceros. No se usan credenciales, cookies privadas
ni endpoints de órdenes. Los metadatos almacenados se limitan a clasificación,
disponibilidad y procedencia; no se guardan enlaces a documentos con claves del servicio.

La documentación de Frankfurter indica que v1 continúa funcionando aunque v2 sea
la versión preferida. Se usa v1 por su serie BCE y formato estable. Si se migra a v2,
debe fijarse el proveedor BCE en vez de mezclar autoridades de cambio.

Yahoo es un agregador gratuito, no una fuente oficial del emisor. Las limitaciones
de cobertura y correcciones se conservan en `quality.csv` y `failures.csv`.
Se debe respetar el uso personal y las condiciones de cada proveedor; el repositorio
no redistribuye las series descargadas.

## Selección de 250 y alternativas revisadas

Comprobación en vivo el **5 de noviembre de 2026**: las tres primeras páginas de
`neonSearch` (búsqueda vacía, 100 resultados por página, jurisdicción ES) devolvieron
300 ISIN distintos por tipo. Se conservan las primeras 250 apariciones, sin ordenar
por ISIN ni partir la búsqueda por gestora. TR publicó 11.323 acciones, 944 fondos
tradicionales y 2.947 candidatos a ETF. Esos conteos son una instantánea cambiante.
El orden predeterminado se usa como **proxy de relevancia**: TR no documenta su
fórmula y no se interpreta como ranking por patrimonio, volumen o calidad.

La caché distingue el máximo configurado y la enumeración completa anterior.
`selection_complete_by_type` acredita la recuperación de la selección solicitada,
no los históricos de sus integrantes. Cada análisis guarda `selection.csv`, los
fallos de precios y su cobertura por separado. Un ETF que no acredite UCITS o un
ISIN sin datos no se reemplaza por el siguiente del explorador.

Yahoo devolvió `quoteType=ETF` al buscar por ISIN `LU0099574567`, `DE0005152441` y
`DE0005152466`, que el catálogo TR clasifica como fondos tradicionales. Se admite
esa etiqueta del proveedor sin cambiar el tipo del catálogo, manteniendo la búsqueda
exacta, la comprobación inversa cuando existe y el rechazo de identidades ambiguas
o contradictorias. Que exista un ticker no garantiza un histórico descargable.

También se revisaron directamente la API, las fichas y muestras de Hugging Face:

| Dataset | Resultado de la revisión | Decisión |
| --- | --- | --- |
| [mik3ml/timeseries-finance-ETF](https://huggingface.co/datasets/mik3ml/timeseries-finance-ETF) | Muestra con `id`, secuencias `timestamp/target`, `category` y `Ticker`; no aporta ISIN ni divisa/ajuste de distribuciones en esas filas. Última modificación indicada: julio de 2024 | No permite atribuir automáticamente la misma clase europea ni valorar precios actuales |
| [paperswithbacktest/ETFs-Daily-Price](https://huggingface.co/datasets/paperswithbacktest/ETFs-Daily-Price) | Descarga restringida; el README devolvió HTTP 401. La ficha exige aceptación y vincula el acceso a una suscripción | No se añade como fuente gratuita automática |
| [VilaVision/MutualFundclassifierdataset](https://huggingface.co/datasets/VilaVision/MutualFundclassifierdataset) | Conversaciones sobre fondos para entrenar modelos | No contiene históricos NAV utilizables |
| [DenyTranDFW/SEC_Company_Tickers_MF_MutualFunds](https://huggingface.co/datasets/DenyTranDFW/SEC_Company_Tickers_MF_MutualFunds) | Referencia de tickers de fondos SEC; no acredita históricos de las clases TR | No sustituye un histórico del mismo ISIN |
| [sap8979/mutual-fund-db](https://huggingface.co/datasets/sap8979/mutual-fund-db) | Publica un archivo SQLite comprimido sin ficha README | No se presume cobertura, identidad ni ajustes sin documentación |

Esta revisión no prueba que no existan otros datasets adecuados. Los datos externos
de una clase acreditada pueden entrar por el proveedor local ya disponible:
`data/manual/prices/<ISIN>.csv`, con
`date,price,adjusted_price,currency,source_url`. Un NAV sin ajustar de una clase de
distribución no es una serie de retorno total y no debe rellenarse como si lo fuera.
