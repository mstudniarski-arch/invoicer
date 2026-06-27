# Legal-Grounded RAG — Eval Report

- Generated: 2026-06-27
- Cases: **6** (golden dataset, `data/evals/legal_cases.jsonl`)
- Store: **pgvector** · Embeddings: **voyage-3-large** · Reranker: **rerank-2.5**
- **Recall@5: 1.00**
- **MRR: 0.68**

_Retrieval mierzony na `PgVectorLegalStore` (Voyage embed + rerank). Faithfulness + ablacja z/bez RAG: do uzupelnienia._
