# Re-ranking Alternatives: FlashRank vs BGE-Reranker-Base

**Current Implementation**: FlashRank with `ms-marco-TinyBERT-L-2-v2`

Este documento explica las opciones de re-ranking disponibles para Mnemosyne y cómo cambiar entre ellas si los resultados no son óptimos.

---

## Comparación Rápida

| Aspecto | FlashRank (actual) | BGE-Reranker-Base (alternativa) |
|---------|-------------------|----------------------------------|
| **Modelo** | ms-marco-TinyBERT-L-2-v2 | BAAI/bge-reranker-base |
| **Tamaño** | 4 MB (4.3M params) | 1,040 MB (278M params) |
| **Velocidad** | 9,000 docs/seg | Lento en CPU (~10x más lento) |
| **Accuracy** | Bueno (competitivo) | Excelente (mejor performance) |
| **Librería** | FlashRank (separada) | FlagEmbedding (misma que embeddings) |
| **Recursos** | Mínimo (CPU-first) | Alto (mejor con GPU) |
| **Latencia** | Ultra-baja | Mayor (especialmente CPU) |

---

## FlashRank: Implementación Actual

### ¿Por qué FlashRank?

1. ✅ **Velocidad crítica**: 260x más pequeño, ultra-rápido en CPU
2. ✅ **Recursos mínimos**: 4 MB vs 1 GB
3. ✅ **Sorprendentemente bueno**: Compite con modelos 64x más grandes
4. ✅ **Producción-ready**: Diseñado para baja latencia

### Ventajas

- **Speed-optimized**: Procesa hasta 9,000 documentos por segundo
- **CPU-first**: No requiere GPU para buen rendimiento
- **Lightweight**: Minimal footprint (4 MB)
- **Low latency**: Ideal para detección en tiempo real
- **Good enough**: Accuracy competitiva para la mayoría de casos

### Desventajas

- **Accuracy**: Menor que modelos grandes (pero suficiente para la mayoría de casos)
- **English-only**: No soporta otros idiomas
- **Separate library**: Requiere FlashRank además de FlagEmbedding

### Benchmarks

- **TREC Deep Learning 2019**: NDCG@10 = 69.84
- **MS Marco Passage**: MRR@10 = 32.56
- **Inference Speed**: 9,000 docs/sec en CPU

---

## BGE-Reranker-Base: Alternativa de Alta Precisión

### ¿Cuándo considerar BGE-Reranker-Base?

- ✅ Tienes GPU disponible
- ✅ Priorizas accuracy sobre velocidad
- ✅ Quieres consolidar en una sola librería (FlagEmbedding)
- ✅ Necesitas soporte multilingüe
- ✅ FlashRank no detecta duplicados con suficiente precisión

### Ventajas

- **Higher accuracy**: Mejor performance en benchmarks
- **Same library**: FlagEmbedding (misma que bge-large-en-v1.5)
- **Multilingual**: Soporta inglés + chino
- **State-of-the-art**: Parte de la familia BGE (top-tier)
- **Fine-tunable**: Puede ajustarse con 2000 queries

### Desventajas

- **Large model**: 1,040 MB (278M parámetros)
- **Slow on CPU**: ~10x más lento que FlashRank
- **Higher memory**: Requiere más RAM
- **Better with GPU**: Rendimiento óptimo requiere GPU

---

## Cómo Cambiar a BGE-Reranker-Base

### Paso 1: Instalar FlagEmbedding

Si aún no está instalado:

```bash
uv pip install -U FlagEmbedding
```

### Paso 2: Modificar `src/mnemosyne/core/search.py`

Reemplaza la sección de re-ranking:

```python
# ANTES (FlashRank)
from flashrank import Ranker, RerankRequest

class HybridSearcher:
    def _get_reranker(self) -> Ranker:
        if self.reranker is None:
            logger.info(f"Loading reranker: {self.settings.rerank_model}")
            self.reranker = Ranker(
                model_name=self.settings.rerank_model,
                cache_dir=".cache/flashrank"
            )
        return self.reranker

    def rerank_results(self, query: str, candidates: List[SearchResult], top_k: int = 10):
        # ... preparar passages ...

        reranker = self._get_reranker()
        rerank_request = RerankRequest(query=query, passages=passages)
        reranked = reranker.rerank(rerank_request)

        # ... procesar resultados ...
```

```python
# DESPUÉS (BGE-Reranker-Base)
from FlagEmbedding import FlagReranker

class HybridSearcher:
    def _get_reranker(self) -> FlagReranker:
        if self.reranker is None:
            logger.info(f"Loading reranker: BAAI/bge-reranker-base")
            self.reranker = FlagReranker(
                'BAAI/bge-reranker-base',
                use_fp16=True  # Faster inference with minimal accuracy loss
            )
        return self.reranker

    def rerank_results(self, query: str, candidates: List[SearchResult], top_k: int = 10):
        # ... preparar passages ...

        reranker = self._get_reranker()

        # Create query-document pairs
        pairs = [[query, p["text"]] for p in passages]

        # Get scores
        scores = reranker.compute_score(pairs)

        # Zip scores with passages and sort
        scored_passages = [
            {**p, "score": score}
            for p, score in zip(passages, scores)
        ]
        reranked = sorted(scored_passages, key=lambda x: x["score"], reverse=True)

        # ... procesar resultados ...
```

### Paso 3: Actualizar configuración en `.env`

```bash
# Cambiar el modelo de re-ranking
RERANK_MODEL=BAAI/bge-reranker-base

# Opcional: Reducir top_k si la latencia es alta
RERANK_TOP_K=5  # Reducir de 10 a 5 para acelerar
```

### Paso 4: Limpiar cache y probar

```bash
# Eliminar cache de FlashRank
rm -rf .cache/flashrank

# Probar con un reporte
uv run mnemosyne scan data/raw/55140_Race_Conditions_in_OAuth_2_API_implementations.md
```

---

## Optimizaciones para BGE-Reranker-Base

Si decides usar BGE-Reranker-Base, aquí hay optimizaciones recomendadas:

### 1. Usar FP16 (Half Precision)

```python
reranker = FlagReranker('BAAI/bge-reranker-base', use_fp16=True)
```

**Beneficio**: ~2x más rápido con ~0.1% pérdida de accuracy

### 2. Reducir Top-K

En `.env`:
```bash
RERANK_TOP_K=5  # En vez de 10
```

**Beneficio**: 50% menos inferencias del modelo

### 3. Batch Processing

Si procesas múltiples queries, usa batching:

```python
# En lugar de compute_score() múltiples veces
all_pairs = []
for query in queries:
    for doc in documents:
        all_pairs.append([query, doc])

scores = reranker.compute_score(all_pairs)
```

### 4. GPU Acceleration (si disponible)

BGE-Reranker-Base se beneficia enormemente de GPU:

```python
import torch
device = "cuda" if torch.cuda.is_available() else "cpu"

reranker = FlagReranker(
    'BAAI/bge-reranker-base',
    use_fp16=True,
    device=device
)
```

---

## Métricas de Performance Esperadas

### FlashRank (ms-marco-TinyBERT-L-2-v2)

**10 candidatos en CPU:**
- Latencia: ~10-20ms
- Throughput: 9,000 docs/seg
- Memory: ~50 MB
- CPU Usage: Bajo (~5-10%)

### BGE-Reranker-Base

**10 candidatos en CPU:**
- Latencia: ~200-500ms
- Throughput: ~50-100 docs/seg
- Memory: ~1.2 GB
- CPU Usage: Alto (~50-80%)

**10 candidatos en GPU:**
- Latencia: ~20-50ms
- Throughput: ~1,000-2,000 docs/seg
- Memory (GPU): ~2 GB VRAM
- GPU Usage: Moderado (~20-30%)

---

## Cómo Decidir Qué Usar

### Usa FlashRank si:

- ✅ Corres en CPU sin GPU
- ✅ Necesitas baja latencia (<50ms)
- ✅ Procesas reportes uno a uno
- ✅ Recursos limitados (RAM, CPU)
- ✅ La accuracy actual es suficiente (detecta la mayoría de duplicados)

### Cambia a BGE-Reranker-Base si:

- ✅ Tienes GPU disponible
- ✅ FlashRank no detecta duplicados obvios
- ✅ Priorizas precision sobre velocidad
- ✅ Procesas reportes en batch
- ✅ Quieres consolidar en FlagEmbedding

---

## Testing de Cambios

Después de cambiar el modelo de re-ranking, prueba con estos casos:

### 1. Test de Duplicado Exacto

```bash
# Debe detectar como DUPLICATE con score ~0.95+
uv run mnemosyne scan data/raw/55140_Race_Conditions_in_OAuth_2_API_implementations.md
```

### 2. Test de Reporte Similar

```bash
# Crear un reporte similar pero con diferente componente
# Debe detectar como SIMILAR (0.65-0.85)
```

### 3. Test de Reporte Nuevo

```bash
# Crear un reporte de un tipo de vulnerabilidad no indexado
# Debe detectar como NEW (<0.65)
```

### 4. Benchmark de Latencia

```bash
# Comparar tiempos de ejecución
time uv run mnemosyne scan data/raw/report1.md
time uv run mnemosyne scan data/raw/report2.md
time uv run mnemosyne scan data/raw/report3.md
```

---

## Referencias

### FlashRank

- [GitHub - PrithivirajDamodaran/FlashRank](https://github.com/PrithivirajDamodaran/FlashRank)
- [ms-marco-TinyBERT Model Card](https://huggingface.co/cross-encoder/ms-marco-TinyBERT-L2-v2)

### BGE-Reranker-Base

- [GitHub - FlagOpen/FlagEmbedding](https://github.com/FlagOpen/FlagEmbedding)
- [BAAI/bge-reranker-base Model Card](https://huggingface.co/BAAI/bge-reranker-base)
- [BGE Reranker Documentation](https://bge-model.com/tutorial/5_Reranking/5.2.html)

---

## Soporte

Si tienes problemas o dudas sobre el re-ranking:

1. **Check logs**: Usa `--verbose` para ver detalles del re-ranking
2. **Compare scores**: Observa los scores antes y después del re-ranking
3. **Test incrementally**: Cambia solo el modelo, luego ajusta parámetros

**Recomendación**: Empieza con FlashRank. Solo cambia si observas problemas de accuracy específicos.
