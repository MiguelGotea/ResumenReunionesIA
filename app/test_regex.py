import re
import json

texto = '''{
  "resultado_final": "## Decisiones Tomadas\n* Tarea 1\n* Tarea 2",
  "resumen": "La reunión de hoy..."
}'''

def extraer_campo(campo, texto_completo, es_ultimo=False):
    patron = f'"{campo}"\\s*:\\s*"((?:\\\\.|[^"\\\\])*)'
    if not es_ultimo:
        patron += '"'
        
    print("PATRON:", patron)
    match = re.search(patron, texto_completo)
    if match:
        val = match.group(1)
        if val.endswith('\\'):
            val = val[:-1]
        val = val.replace('\\n', '\n').replace('\\"', '"').replace('\\t', '\t')
        return val
    return 'FALLO'

print('rf:', extraer_campo('resultado_final', texto))
print('res:', extraer_campo('resumen', texto, True))
