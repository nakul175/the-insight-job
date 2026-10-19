# AI, model and agent application engineering


**Package:** APLYD-ENG-001 · **Version:** 2.1.0 · **Date:** 11 October 2026

These controls cover LLMs, predictive models, vision, speech and tool-using agents. They govern implementation and operation, not model-evaluation scenarios or clinical validation.


## Controls


<a id="eng-ai-001"></a>

### ENG-AI-001 — Maintain an inference manifest

**Requirement:** MUST · **Review:** human · **Evidence:** Model/inference manifest

Record use case, provider/model/deployment identifier, version or immutable artifact digest when available, runtime, input/output schema, prompt/preprocessing/postprocessing versions, inference settings, permitted data classes, usage rights and limitations. For floating provider aliases, record the resolved version when returned; otherwise explicitly record it as unavailable.


<a id="eng-ai-002"></a>

### ENG-AI-002 — Version prompts and transformations

**Requirement:** MUST · **Review:** human · **Evidence:** Versioned manifest and PR

Keep production prompts, tool schemas, retrieval configuration, feature extraction, preprocessing and postprocessing under reviewed version control. Separate instructions from untrusted user/retrieved content. A prompt change that changes behaviour is an engineering change, not untracked copy editing.


<a id="eng-ai-003"></a>

### ENG-AI-003 — Treat model output as untrusted

**Requirement:** MUST · **Review:** human · **Evidence:** Output boundary and failure contract

Validate structured output against the actual runtime schema and enforce bounds before consuming it. Never execute generated SQL, shell, HTML, file paths or tool arguments directly. Distinguish unsupported, indeterminate, refused, malformed and failed results from successful inference.


<a id="eng-ai-004"></a>

### ENG-AI-004 — Enforce action authority outside the model

**Requirement:** MUST · **Review:** human · **Evidence:** Tool gateway and approval binding

Use narrow allowlisted tools, explicit tenant/actor context, independently enforced permissions and bounded arguments. Consequential actions require the appropriate confirmation/approval bound to exact parameters. Model text, retrieved documents and another agent cannot confer authority or lift budgets.


<a id="eng-ai-005"></a>

### ENG-AI-005 — Preserve data policy through retrieval and inference

**Requirement:** MUST · **Review:** human · **Evidence:** Data-flow policy and retrieval implementation

Apply tenant/ACL filtering before retrieval and recheck at delivery where necessary. Carry deletion/revocation into indexes and caches. Verify destination, retention and data-use authority before calling a provider; never assume a coding subscription permits production-data processing.


<a id="eng-ai-006"></a>

### ENG-AI-006 — Bound spend and computation

**Requirement:** MUST · **Review:** human · **Evidence:** Budget/configuration and runbook

Set request, token/input size, tool-step, concurrency, retry and cost limits with accountable ownership. Define cancellation and budget exhaustion states. Do not introduce unbounded agent loops, silent paid-provider fallbacks or new paid services without approval.


<a id="eng-ai-007"></a>

### ENG-AI-007 — Be honest about fallback and evidence

**Requirement:** MUST · **Review:** human · **Evidence:** Response provenance and intended-use record

A fallback model, deterministic rule or manual path must be approved for the use case and visibly identified in provenance. Never fabricate a plausible result when inference fails. Do not present a demo model as validated for clinical or consequential decisions.


<a id="eng-ai-008"></a>

### ENG-AI-008 — Make inference attributable without oversharing

**Requirement:** MUST · **Review:** human · **Evidence:** Observability schema and retention policy

Record correlation ID, model/config versions, processing status, latency, usage/cost metadata and outcome references appropriate to policy. Do not log raw protected prompts, audio, images or full responses by default. Record sources/provenance for retrieved claims where the product contract requires them.


<a id="eng-ai-009"></a>

### ENG-AI-009 — Protect model artifacts and execution

**Requirement:** MUST · **Review:** human · **Evidence:** Artifact/dependency decision and isolation design

Load model artifacts from approved sources with integrity/version checks. Treat custom code and unsafe deserialisation as executable supply-chain risk; isolate untrusted workloads. Record licences for code, weights and data separately. An open repository does not establish all reuse rights.


<a id="eng-ai-010"></a>

### ENG-AI-010 — Control model lifecycle changes

**Requirement:** MUST · **Review:** human · **Evidence:** Model change record and release decision

A model/provider/preprocessing/prompt change must identify behaviour, data, cost, compatibility and rollback implications. Route affected assurance/intended-use decisions to their owner. This coding package does not define or replace the independent evaluation evidence needed for the use case.


## Implementation guidance

### Minimal manifest shape
Use JSON/YAML or an existing registry with these fields: manifest ID; intended use and exclusions; model/artifact identity; provider and processing region; input/output contract versions; prompt/preprocessing/postprocessing versions; tool allowlist; data-policy reference; inference parameters; limits; fallback; observability schema; owner; change/approval references.

### Reproducibility boundary
Capture enough configuration to explain an execution. Do not promise identical output from stochastic models, provider-managed versions or non-deterministic hardware. A seed is useful metadata where supported, not a universal reproducibility guarantee.

### Independent organisations
An orchestration app and an AI product retain separate credentials, tenant mappings, contracts, rights and operational ownership. Use a connector and scoped service identity, not shared administrator access. Expose true connection/inference status and explicit result limitations.


## Reference basis

S04 informs prompt-injection risks. These controls are APLYD implementation requirements, not an assertion that prompt filtering eliminates those risks. See the package [source register](../SOURCES.md). These references do not establish product certification.
