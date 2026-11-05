Opcional: un CSV por ISIN, por ejemplo `IE00XXXXXXXX.csv`, procedente de la gestora.
Columnas: `date,price,adjusted_price,currency,source_url`.

`price`: cierre/valor liquidativo sin ajustar por dividendos; `adjusted_price`:
serie de retorno total, con reinversión de distribuciones y splits corregidos.
En una clase de acumulación puede coincidir con el valor liquidativo. En una clase
de distribución NO basta el NAV sin ajustar para comparar rendimientos.
Todas las filas deben tener la misma divisa y URL de fuente. Fechas ISO.
No se inventa un ticker si Yahoo no cubre la clase exacta.

