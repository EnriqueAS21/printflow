## Reglas de trabajo
- No ejecutes pruebas ni crees pruebas nuevas salvo que el prompt diga "CIERRE DE PASO".
- En "CIERRE DE PASO": corre pytest -q una sola vez. Ignora los 2 fallos conocidos de stickers de tabloide. Reporta solo los fallos nuevos, uno por línea: archivo::prueba — error. No los corrijas.
- Respuesta final breve: lista de archivos modificados con una línea de qué cambió. Sin resúmenes largos.
- No toques archivos fuera de los que menciona el prompt.
- No ejecutes diagnósticos ni revisiones de errores al terminar. No leas ni busques archivos que el prompt no menciona; lee solo los que vas a modificar.