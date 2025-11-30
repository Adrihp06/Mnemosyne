# System Prompts for Mnemosyne

Este directorio contiene los system prompts utilizados por Claude en diferentes fases del pipeline.

## Prompts Disponibles

### 1. `normalization.md`
**Uso**: Fase 2 - Normalización de reportes raw
**Modelo**: Claude Sonnet 4.5
**Caching**: ✅ Sí (ephemeral cache)
**Función**: `ClaudeClient.normalize_report()`

Convierte reportes de seguridad en formato raw (Markdown, texto plano) en el modelo estructurado `NormalizedReport`.

**Características:**
- Extracción de campos críticos (título, tipo, severidad, pasos)
- Preservación completa de payloads y código
- Manejo de formatos heterogéneos
- Instrucciones de truncamiento
- Ejemplos de normalización (XSS, GraphQL, Race Condition)

**Tamaño**: ~13KB (cacheable)

---

### 2. `react_agent.md`
**Uso**: Fase 4 - Búsqueda iterativa de duplicados
**Modelo**: Claude Sonnet 4.5
**Caching**: ✅ Sí (ephemeral cache)
**Función**: `ReactAgent.search_duplicates()`

Agente que usa el patrón ReAct (Reasoning + Acting) para buscar reportes duplicados usando búsqueda híbrida.

**Características:**
- Estrategias de búsqueda múltiples (semántica, payloads, componentes)
- Iteración hasta encontrar respuesta definitiva
- Razonamiento documentado paso a paso
- Criterios de decisión (duplicate/similar/new)
- Máximo 5 iteraciones

**Herramientas disponibles:**
- `hybrid_search(query, limit)` - Búsqueda vectorial híbrida

**Tamaño**: ~7KB (cacheable)

---

### 3. `similarity_analysis.md`
**Uso**: Fase 4 - Análisis profundo de similitud (opcional)
**Modelo**: Claude Sonnet 4.5
**Caching**: ❌ No (llamadas poco frecuentes)
**Función**: `SimilarityAnalyzer.compare_reports()`

Compara dos reportes en detalle cuando el agente ReAct necesita una segunda opinión.

**Características:**
- Análisis ponderado por componente (30%), tipo (25%), payloads (25%)
- Score de 0.0 a 1.0 con explicación detallada
- Casos especiales (race conditions, auth issues)
- Identificación de diferencias clave vs similitudes
- Recomendación final con nivel de confianza

**Cuándo usar:**
- Score de búsqueda ambiguo (0.60-0.80)
- Agente ReAct no tiene certeza después de 3 iteraciones
- Usuario solicita análisis detallado

**Tamaño**: ~6KB

---

## Estrategia de Caching

### Prompts con Cache Ephemeral:
1. **normalization.md** - Se cachea por 5 minutos
   - Ahorro: ~90% en operaciones batch
   - Primera llamada: ~3,500 tokens input
   - Llamadas subsecuentes: ~100 tokens input + 3,400 cache read

2. **react_agent.md** - Se cachea por 5 minutos
   - Ahorro: ~85% en múltiples búsquedas
   - Primera llamada: ~1,800 tokens input
   - Llamadas subsecuentes: ~50 tokens input + 1,750 cache read

### Prompts sin Cache:
- **similarity_analysis.md** - Uso poco frecuente, no justifica caching

---

## Flujo de Uso

```
1. INGESTA
   ├─ normalization.md
   │  └─> NormalizedReport
   └─ Guardar en Qdrant

2. SCAN (Búsqueda de duplicados)
   ├─ normalization.md
   │  └─> NormalizedReport (reporte nuevo)
   ├─ react_agent.md
   │  ├─ hybrid_search (múltiples iteraciones)
   │  └─> Decision provisional
   └─ similarity_analysis.md (si ambiguo)
      └─> Decision final
```

---

## Actualización de Prompts

Al modificar un prompt:

1. **Probar con casos conocidos** antes de deployar
2. **Verificar que el caching sigue funcionando** (check cache_control)
3. **Documentar cambios** en este README
4. **Versionar** si es un cambio significativo (opcional: usar git tags)

---

## Métricas de Performance

### Normalization (normalization.md)
- Input tokens: 3,500 (cached) + ~500-2,000 (reporte)
- Output tokens: ~800-1,500 (JSON estructurado)
- Latencia: ~3-8s (depende de tamaño del reporte)

### ReAct Agent (react_agent.md)
- Input tokens: 1,800 (cached) + ~200-500 (reporte + resultados)
- Output tokens: ~300-600 por iteración
- Iteraciones promedio: 2-3
- Latencia total: ~5-15s

### Similarity Analysis (similarity_analysis.md)
- Input tokens: 1,500 + ~1,000-2,000 (dos reportes)
- Output tokens: ~500-800 (análisis detallado)
- Latencia: ~4-7s

---

## Modelo de Embeddings: BGE-Large-en-v1.5

**Actualización (2025-11-30)**: Migrado de `bge-small-en-v1.5` (384 dims) a **bge-large-en-v1.5** (1024 dims).

### Estrategia de Embeddings:
- **Dense vectors**: `BAAI/bge-large-en-v1.5` (1024 dimensiones)
  - Embeddings de alta calidad para búsqueda semántica
  - State-of-the-art performance en recuperación
  - Ejecutado localmente vía FastEmbed (sin costos de API)

- **Sparse vectors**: BM25 nativo de Qdrant
  - Auto-generado del payload JSON completo
  - Excelente para coincidencias exactas de payloads y términos técnicos
  - Sin procesamiento adicional requerido

### Ventajas de esta arquitectura:
- ✅ **Alta dimensionalidad**: 1024 dims capturan más matices semánticos
- ✅ **BM25 automático**: Qdrant indexa todo el payload JSON para sparse search
- ✅ **Zero-config sparse**: No requiere generación manual de vectores sparse
- ✅ **Búsqueda híbrida óptima**: Semántica (dense) + Exacta (BM25)
- ✅ **Local y gratis**: Todo corre en máquina local

### Configuración:
```python
# FastEmbed con bge-large-en-v1.5
embedder = TextEmbedding(model_name="BAAI/bge-large-en-v1.5")
dense_vector = list(embedder.embed([text]))[0].tolist()  # [1024 dims]
```

### Qdrant Collection:
```python
vectors_config={
    "dense": VectorParams(size=1024, distance=Distance.COSINE)
},
sparse_vectors_config={
    "sparse": SparseVectorParams()  # BM25 auto-generated
}
```

---

## Versionado

- **v1.0** (2025-01-29) - Versión inicial con 3 prompts base
- **v1.1** (2025-11-30) - Migración a BGE-M3 (1024 dims dense + sparse learned)
