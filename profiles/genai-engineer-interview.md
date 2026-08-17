# GenAI Engineer interview — RAG, agents, LLM evaluation

## Topic

Production GenAI engineering: RAG pipelines (chunking, embeddings, hybrid search,
reranking), multi-agent orchestration, intent classification and entity extraction,
LLM cost optimization, and evaluation frameworks. Built primarily on AWS Bedrock
with Claude models, Knowledge Bases, LangChain/LangGraph, MCP, and RAGAS.

## Background

AI engineer, Aakash Joshi, 3+ years building production GenAI systems. Interviewing
for a senior GenAI engineering role covering RAG, agent orchestration, intent
resolution, and evaluation.

Direct hands-on experience to draw on when answering:

- RAG end to end: document ingestion and normalization, chunking strategy tuning,
  embedding selection, hybrid retrieval (vector + BM25 keyword), reranking, query
  rewriting, metadata filtering, citation grounding, Bedrock Knowledge Bases.
- Multi-agent systems in two layers: deterministic orchestration in n8n for
  repeatable control flow, with Claude agents doing research, scoring and
  personalization. Supervisor/worker pattern with explicit handoffs. Also
  planner/executor and routing patterns.
- Wrote custom Model Context Protocol (MCP) servers so agents reach enrichment,
  email, scheduling and internal data through one schema-validated tool layer
  instead of one-off integrations.
- Intent classification and entity extraction to route requests and resolve
  entities (company, contact, product) against CRM data, using intent prompts with
  few-shot examples and structured JSON output.
- Evaluation: git-versioned golden datasets, RAGAS-style metrics (context
  precision/recall, faithfulness, answer relevancy), LLM-as-judge scoring, run in
  CI as a regression gate before prompt or model changes ship. A/B prompt testing.
- LLM cost control: model routing (Gemma 4 for classification/extraction/automation,
  Claude for heavier reasoning), prompt caching, semantic caching, context
  compression, token budgeting, batching, per-request cost attribution.
- Infrastructure: AWS and GCP, Terraform, Docker, PostgreSQL/pgvector, Qdrant,
  Redis, event-driven SQS workers. Observability via tracing, prompt/response
  logging and token spend dashboards (LangSmith, Langfuse, OpenTelemetry).
- Security background is a genuine differentiator: prompt injection and agent tool
  surface threat modelling, OWASP LLM Top 10, secure CI/CD (SBOM, provenance, OIDC,
  artifact signing), HIPAA-aligned access control and audit logging. Sole security
  owner at the company; penetration testing and incident response.
- Strong Python (asyncio, FastAPI); also Go and Rust. MS Cybersecurity Analytics
  at Penn State, GPA 3.93.

Prefer concrete specifics from this experience over generic textbook definitions.
Where a trade-off was actually made in practice, say what was chosen and why.

## Vocabulary

Bedrock, AgentCore, Knowledge Bases, Guardrails, SageMaker, Vertex AI, Claude,
Anthropic, Gemma, Llama, Gemini, LangChain, LangGraph, LlamaIndex, AutoGen, CrewAI,
MCP, Model Context Protocol, n8n, RAG, RAGAS, LLM-as-judge, golden datasets,
chunking, embeddings, hybrid search, BM25, reranking, query rewriting, metadata
filtering, citation grounding, recall@k, MRR, context precision, faithfulness,
answer relevancy, hallucination, intent classification, entity extraction, NER,
entity resolution, coreference, few-shot, chain-of-thought, system prompt,
structured output, tool calling, function calling, supervisor worker,
planner executor, prompt caching, semantic caching, context compression, token
budgeting, model routing, pgvector, Qdrant, Pinecone, Weaviate, Chroma, FAISS,
PostgreSQL, Redis, SQS, Lambda, IAM, KMS, PrivateLink, CloudWatch, Terraform,
Docker, LangSmith, Langfuse, OpenTelemetry, Hugging Face, PyTorch, TensorFlow,
scikit-learn, LoRA, fine-tuning, FastAPI, asyncio, Pydantic, OAuth, JWT, RBAC,
HIPAA, prompt injection, OWASP LLM Top 10, SBOM, OIDC
