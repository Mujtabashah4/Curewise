# ADR-000 — Scope of the AI platform layer

- **Status:** Accepted
- **Date:** 2026-09-10
- **Deciders:** Project owner
- **Supersedes:** nothing

---

## Context

CureWise has five AI features across five provider call sites, six independent
client singletons and five hardcoded model IDs, with **no timeouts, no retries,
no failover, no token accounting, no output enforcement and no observability**.
Scope control ("do not diagnose") exists only as text inside prompts.

Four previously-built platform projects were candidates for integration as a
unified AI platform layer:

| Project | Built against |
|---|---|
| `llm-gateway` | Bedrock + Anthropic, multi-tenant, API-key auth |
| `llm-guardrails` | injection / PII / toxicity / secrets detectors |
| `llm-observability` | SQLite trace store capturing prompts and completions |
| `rag-evaluation-framework` | Bedrock + Titan + Chroma, RAGAS, NASA corpus |

An audit of CureWise and of all four project sources established:

1. **Zero stack overlap.** CureWise runs OpenAI + Groq + Pinecone. The projects
   target Bedrock/Anthropic and Chroma. The gateway's provider interface,
   `complete(prompt: str, max_tokens: int)`, cannot express four of CureWise's
   five call sites (system roles, multimodal input, JSON mode) and cannot
   express embeddings at all.
2. **The guardrail taxonomy is not medical.** Measured: 5 of 6 realistic
   patient-crisis inputs pass the detectors unflagged. PII redaction removes a
   phone number and leaves the clinical content untouched — inverted for a
   health product. The bundled benchmark reports macro-F1 **1.000** on 26 cases
   authored alongside the detectors.
3. **Observability violates a non-negotiable constraint by design.** Spans
   persist prompt and completion text; there is no redaction or opt-out. Its
   most relevant evaluator (`groundedness`) only functions when that text is
   stored, and returns `passed: True` when it is absent.
4. **The two most serious defects in CureWise are fixed by none of the four**:
   an assistant that boots and answers ungrounded when the vector index is
   empty, and a safety disclaimer that exists only as a React component while
   the API returns unqualified model output.
5. **The image classifiers are untouched by all four**, despite being the
   feature the product leads with.

The thesis under test was: *in a health context the model is the easy part, and
the platform around it is what makes it safe to ship.*

## Decision

**The thesis holds for CureWise. The inventory does not.**

The platform layer will be built from CureWise's own requirements. Component
disposition:

| Component | Disposition | Basis |
|---|---|---|
| `llm-gateway` — provider abstraction | **Adopted as design, rewritten** | Concern real (no timeouts/retries/accounting); code not portable — wrong providers, insufficient interface |
| `llm-gateway` — gateway surface | **Declined** | Single first-party consumer; tenancy, API-key auth and quotas solve a problem that does not exist |
| `llm-guardrails` | **Adopted as framework, rewritten** | `Guard`/`Verdict`/`Policy` architecture, `evaluate.py` methodology and `injection.py` transfer; toxicity/PII/secrets discarded or inverted |
| `rag-evaluation-framework` | **Adopted as method, not as code** | Experiment design is sound; infrastructure and corpus do not transfer; synthetic test-set generator dropped in favour of held-out MedQuAD pairs with real ground truth |
| `llm-observability` | **Declined as written; rebuilt reduced** | Prompt/completion capture is prohibited by constraint |
| `vector-db-benchmark` | **Out of scope** | Index decision already made and paid for (17,713 vectors ingested) |

**Two capabilities are net-new**, present in none of the four and required by
CureWise specifically:

- **Medical scope enforcement** — detect and stop diagnostic assertions, dosage
  recommendations and claims that contradict the disclaimer. Scope boundaries in
  code, not prompts.
- **Crisis detection and escalation** — recognise acute risk and route to
  resources. Escalates (not blocks) on detection, and escalates on detector
  error; this inverts the usual fail-closed direction and is deliberate.

**Three live defects are fixed first, independently of all of the above**: the
per-request grounding gate, the API-level disclaimer, and the classifier
presentation and confidence defects.

## Alternatives rejected

**Integrate all four as built.** Rejected. Zero stack overlap makes "integration"
a rewrite in three of four cases while carrying the original's framing. It would
also import a constraint violation (observability) and a detector set that
measurably misses the failures that matter in this domain. A component present
but unjustified has to be defended to reviewers and is not defensible.

**Integrate the three that fit and drop observability.** Rejected as still
inventory-led. It leaves both net-new capabilities unbuilt and both live safety
defects unfixed, while producing a platform layer whose largest gap — medical
scope enforcement — is exactly the thing a medical product cannot ship without.

**Adopt `llm-gateway` whole as the single entry point for all LLM calls.**
Rejected. Requires a second auth system for one caller, and the provider
interface would have to be replaced regardless. Failover is additionally invalid
for embeddings: substituting the provider changes the vector space and would
return meaningless neighbours against an index built with `text-embedding-3-small`.

**Keep `llm-observability` with io capture disabled.** Rejected. Disabling
capture does not degrade `groundedness`, it silently makes it pass everything.
An evaluator that is unsafe to keep and unsafe to disable is removed, and
groundedness moves offline into the eval harness where ground truth exists and
no PHI is involved.

**Defer the safety fixes behind the integration work.** Rejected. The grounding
gate, the API disclaimer and the classifier fixes depend on none of the four
projects, address defects that are live now, and are hours-to-days of work.

## Consequences

- One PHI store. `general_chat_history` remains the only place prompt and
  completion content is persisted. The metrics plane carries `request_id`,
  model, tokens, latency, cost and status, and joins to the application database
  only when an investigation requires content.
- Delivery is honestly described as **two components substantially rewritten,
  one adopted as method, two capabilities built new, one declined on constraint
  grounds, one out of scope** — not as four integrations.
- Build order: safety fixes → provider module → guardrails (scope and crisis
  first, injection second) → RAG evaluation → reduced observability.
  Evaluation follows guardrails so it can measure their cost, not precede them.
- A retention policy for PHI is now owed. It was already owed; moving
  blood-report context from a one-hour in-memory TTL to durable Postgres
  increased persistence without one. Tracked in `docs/LIMITATIONS.md`.
- New work is required that no existing project supplies: medical-scope and
  crisis detectors, a labelled medical guardrail test set, chunk-level source
  provenance, and prompt/model version pinning per response.

## Open items

- **ADR-001 — MedQuAD corpus.** Whether `data/rag/data.csv` should be committed
  at all. It reinstates CDC/NCI answers that canonical MedQuAD withdrew for
  copyright, and drops the attribution columns upstream carries. Options: commit
  with a provenance header / vendor a filtered subset / fetch-and-transform at
  build time. The third also restores the metadata that answer citation needs.
- **Jurisdiction/locale configuration.** Crisis escalation needs region-specific
  resources; CureWise has no locale concept.
- **Whether the classifier abstention threshold is per-model or global**, and
  what evidence sets it, given no model has measured performance.
