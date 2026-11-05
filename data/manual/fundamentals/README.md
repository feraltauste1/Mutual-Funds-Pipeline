Opcional: `ISIN.csv`, con datos anuales de informes públicos de la compañía.
Columnas: `fiscal_date,eps,currency,split_adjusted,source_url`.

EPS anual diluido, en la divisa de la cotización elegida. `split_adjusted=true`
declara que TODO el histórico está expresado en la misma base de acciones actual
(ajustado por todos los splits posteriores). Conserva la URL del informe emisor.
Esta fuente permite reemplazar EPS ausente o ambiguo en Yahoo. Sin esa garantía,
el pipeline no compara PER históricos de compañías con splits en la ventana.
