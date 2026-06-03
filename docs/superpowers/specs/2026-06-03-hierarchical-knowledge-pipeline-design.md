# Hierarchical Knowledge Pipeline Design

## Background

The current knowledge pipeline archives inbox documents almost verbatim into `knowledge/` and maintains flat topic pages by appending article-specific insights. That no longer matches the desired outcome:

1. Topics must form a multi-level hierarchy with parent/child relationships across different granularities.
2. Knowledge pages must be newly organized summaries derived from inbox `key_points` and body content, not copies of the original article.
3. Topic pages must accumulate topic knowledge itself, not explain why an article belongs to a topic.
4. Every topic level must maintain its own knowledge, not just leaf topics.
5. When the topic tree changes, all affected nodes and mappings must be updated consistently.
6. The system needs a full-library diagnosis/repair script to prevent long-term drift.
7. All new or changed LLM prompts in this redesign must be configurable from `.env`, with code-provided defaults.

## Goal

Produce a hierarchical, self-healing knowledge base in Obsidian where:

- `inbox/` remains the immutable source of truth
- `knowledge/` contains distilled knowledge notes instead of copied source material
- `knowledge/_topics/` contains layered topic knowledge that grows across articles and across topic levels
- topic hierarchy changes can trigger local re-aggregation of affected subtrees
- a repair tool can detect and fix structural drift

## Chosen Approach

Use a **hierarchical knowledge graph pipeline**:

- Automatically evolve topic hierarchy with the model
- Generate derived knowledge notes from structured article distillation
- Maintain topic pages per hierarchy node
- Update topic pages with a **dual-zone strategy**:
  - rewrite the **summary zone** only when new knowledge changes the topic-level understanding
  - append/insert genuinely new material into the **detail zone**
- Add a full-library audit/repair pass for long-term consistency

This approach is larger than a flat-tag upgrade, but it is the only one that directly supports layered topic knowledge, subtree recalculation, and long-lived topic evolution.

## High-Level Architecture

### Source of Truth

`inbox/` remains authoritative. The pipeline must treat inbox notes as immutable source material and never depend on `knowledge/` or topic pages as the only copy of facts.

### Derived Layers

The redesigned pipeline produces three derived layers:

1. **Topic graph**
   - persisted under `_meta/topic-graph.json`
   - stores topic nodes, parent/child relationships, aliases, lineage, and generation metadata
2. **Knowledge notes**
   - distilled article-level notes under `knowledge/`
   - represent organized takeaways, methods, criteria, risks, and selected excerpts
3. **Topic pages**
   - one page per topic node under `knowledge/_topics/`
   - aggregate knowledge at every level, not only leaf levels

### Core Processing Flow

For each inbox note:

1. Parse frontmatter, `key_points` if present, and body content
2. Distill the source into structured knowledge units
3. Map those units onto one or more hierarchical topic paths
4. Update the topic graph if the hierarchy evolves
5. Generate a distilled knowledge note
6. Update all touched topic nodes, including ancestors
7. If hierarchy changed, re-aggregate the affected subtree and impacted mappings

## Topic Graph Design

### File

`_meta/topic-graph.json`

### Node Contract

Each node should have at least:

- `id`: stable identifier
- `name`: current display name
- `parent_id`: nullable parent pointer
- `children_ids`: ordered child list
- `aliases`: synonymous labels or renamed forms
- `path`: canonical hierarchical path
- `replacement_target_id`: canonical successor when this node is merged/replaced
- `lineage`: prior canonical IDs or paths needed for rebuild/migration traceability
- `summary_version`: hash/version used to know whether summary recomputation is stale
- `detail_version`: version used for detail-zone accumulation
- `status`: active / merged / renamed / deprecated

### Key Rules

1. Every level is a first-class topic node.
2. A deep topic automatically contributes knowledge to all ancestors.
3. Topic restructuring is allowed:
   - move node under a new parent
   - split one node into multiple nodes
   - merge overlapping nodes
   - rename a node while preserving alias history
4. Any structural change must trigger recalculation for the impacted node set:
   - changed node
   - ancestors
   - descendants
   - knowledge-note mappings that reference those paths

### Safety Gate for Graph Mutation

The system must distinguish between:

1. **path proposal** — the model proposes create / rename / merge / split / move
2. **graph mutation** — the system decides whether to apply that proposal

Automatic mutation is allowed only when confidence is high and the impact radius is small. Otherwise the system must fall back to a conservative mode:

- keep the existing canonical path
- record the proposed change for later review
- store aliases or pending-mutation metadata instead of immediately rewriting the tree

If the proposal is deferred for a brand-new note and no exact canonical node exists yet, the note is temporarily placed under the nearest existing canonical ancestor path and records the proposed deeper path in metadata/pending mutation state.

### Automatic Mutation Threshold

For this feature, automatic mutation is limited to deterministic low-blast-radius cases:

- model confidence score `>= 0.85`
- impacted existing nodes `<= 5`
- no more than one existing canonical path is replaced in the same mutation

Mutations outside that envelope must not be auto-applied.

### Auto-Apply Mutation Types

Only these mutation types may auto-apply:

- create a new leaf node under an existing canonical parent
- add alias metadata to an existing node

These mutation types are always deferred:

- rename existing canonical node
- merge
- split
- move existing node to a new parent
- replace one canonical node with another

### Rename Semantics

A rename keeps the same node ID.

- `name` changes
- old names move into `aliases`
- canonical path may change
- lineage records the prior canonical path

Rename does not create a replacement node unless it is actually a merge/replacement operation.

### Impact Radius Definition

`impacted existing nodes` means the count of active canonical topic nodes whose page, canonical path, or source-topic mapping would need to change if the mutation were applied.

### Confidence Contract

The mutation-producing prompt must return a normalized `0.0 - 1.0` confidence field. The system uses that numeric field directly; planning does not invent another confidence source.

### Deferred Mutation Store

Deferred mutations are persisted in:

`_meta/pending-topic-mutations.json`

Each pending record must include:

- proposed mutation type
- lifecycle status (`pending` / `superseded` / `rejected`)
- affected node IDs / paths
- confidence score
- reason / evidence summary
- supporting source note paths
- supporting source count
- created timestamp
- resolved timestamp when applicable

### Deferred Mutation Lifecycle

Pending mutations follow explicit lifecycle states:

- `pending`: proposed but not yet eligible
- `superseded`: replaced by a newer canonical decision
- `rejected`: explicitly declined

A pending mutation is **not** auto-promoted later. It stays pending until explicitly handled by repair/apply workflow or manual review.

### Pending Mutation Review Contract

Pending mutations may be resolved only by:

- an explicit repair/apply mode that includes pending-mutation handling, or
- a manual acceptance/rejection action recorded back into metadata state

### Proposal Equivalence Rule

Two proposals are considered the same proposal shape only when all of the following match:

- same mutation type
- same affected canonical node IDs (or unresolved names before first creation)
- same target parent path or target replacement node

### Independent Source Rule

“Independent source notes” means distinct inbox note paths. Reprocessing the same inbox note does not increase support count.

## Knowledge Note Design

### Placement

Knowledge notes are stored by canonical topic path, e.g.:

`knowledge/<topic-path>/<YYYY-MM-DD-title>.md`

The exact directory path is derived from the primary canonical topic path selected for the note.

### Stable Knowledge Note ID

Each knowledge note must have a stable `knowledge_note_id` stored in metadata and mapping state.

Generation rule:

- prefer hash of `(source URL + published date)` when source URL exists
- otherwise use hash of `(persisted first-seen inbox path + published date)`
- if published date is missing, use `(source identity + title)` instead

The note ID is the canonical identity for dedupe and remapping. Filesystem path and display title may change without changing the note ID.

`persisted first-seen inbox path` must be recorded in metadata state the first time the source is seen and reused on later rebuilds/renames.

### Path Encoding Rule

- canonical identity is node-ID-based in metadata, not filename-based
- filesystem path is produced by joining topic path segments with directory separators
- each path segment is slugged with the existing safe-filename rules used by the project
- reserved filesystem characters must be normalized during slugging
- node rename changes the rendered path, but prior path history remains traceable through aliases and mapping state
- if multiple notes would render to the same path, append a short suffix derived from `knowledge_note_id`

### Purpose

A knowledge note is not a copy of the source note. It is a derived note that reorganizes knowledge extracted from the source.

### Required Content Shape

Each note should contain:

- concise topic-oriented summary
- distilled methods / frameworks / criteria / decision rules
- risks / caveats / anti-patterns
- selected supporting excerpts only when they help preserve nuance
- source links back to:
  - the inbox note
  - the original external URL when available

### Explicit Non-Goal

The note must **not** directly preserve the original article body, full transcript, or large copied sections from inbox content.

### Recommended Sections

- `# Title`
- `## 核心结论`
- `## 方法 / 框架`
- `## 判断标准`
- `## 风险与边界`
- `## 关键摘录`
- `## 来源`

### Metadata

Knowledge note metadata should include:

- knowledge note ID
- source inbox path
- source URL
- topic path list
- primary topic path
- generation version
- update timestamp
- topic-node IDs involved

### Cardinality Rule

One source article produces **one primary knowledge note**.

- that note has one primary canonical topic path used for file placement
- it may reference additional secondary topic paths in metadata
- ancestor topics inherit from the same note through mapping, not by duplicating the note into multiple files

This keeps storage, rebuilds, migration, and repair logic stable.

### Secondary Topic Rule

Secondary topic paths are cross-topic aggregation links.

- the note is still stored only once at the primary path
- each secondary topic path contributes to:
  - that secondary node
  - ancestors of that secondary node
- dedupe is done at the note ID + topic node ID level so one note cannot double-count within the same node aggregate

### Primary Path Stability Rule

When the topic hierarchy changes, the note keeps exactly one primary path by deterministic priority:

1. keep the current primary path if it still resolves to an active canonical node
2. otherwise move to the deepest surviving canonical ancestor
3. otherwise move to the canonical node explicitly designated as the merge target
4. otherwise move to the highest-confidence replacement path produced during rebuild

Any previous primary path must remain traceable through aliases or source-topic mapping history so links and repairs can follow the move.

## Topic Page Design

### Placement

One topic page per topic node:

`knowledge/_topics/<topic-path>.md`

This means parent topics and child topics both have their own pages and both accumulate knowledge.

### Purpose

A topic page should describe the topic itself:

- what the topic means
- what patterns or frameworks have emerged
- what the important distinctions are
- what new details/examples have accumulated
- how it relates to parent and child topics

It should **not** narrate article assignment reasons.

### Dual-Zone Update Strategy

Each topic page is split into two logical zones:

1. **Summary zone**
   - definition
   - core framework
   - key conclusions
   - parent/child topic relations
   - rewritten only when new information materially changes topic-level understanding
2. **Detail zone**
   - new examples
   - cases
   - edge conditions
   - nuanced additions
   - incrementally inserted when genuinely new material appears

### Summary Rewrite Rule

The summary zone is rewritten only when the new aggregate changes at least one canonical summary facet for the topic:

- topic definition
- core framework or ordered method
- decision rule / judgment criterion
- risk boundary
- parent/child relation understanding

If none of those facets change, the summary zone must stay untouched.

### Detail Append Rule

Material is “genuinely new” only when its normalized detail fingerprint does not already exist for that topic node.

The fingerprint must be derived from:

- detail type (`example` / `case` / `exception` / `quote` / `tactic`)
- normalized semantic statement

Source paths are tracked as supporting evidence metadata, but they are not part of the uniqueness fingerprint. The same semantic detail supported by additional sources should enrich evidence, not duplicate the detail row.

### Recommended Sections

The following sections are **required** for every topic page:

- `# Topic`
- `## 概览`
- `## 核心框架`
- `## 关键结论`
- `## 上下位关系`
- `## 详情积累`
- `## 关联来源`

`关联来源` should be links only, not article-assignment commentary.

## Distillation and Aggregation Rules

### Article Distillation

The pipeline must read both:

- source `key_points` if present
- source body

Then distill them into structured knowledge units such as:

- concepts
- methods
- decision rules
- risk controls
- examples
- quotes/excerpts

The distillation step should prefer semantic organization over article order.

### Parsed Summary Source

In this spec, `summary field` means the parser-normalized short summary extracted from source metadata/frontmatter when present. It is separate from the new generated knowledge note summary.

### Eligible Source Rule

An inbox note is eligible for knowledge generation only when:

- frontmatter parsing succeeds, and
- at least one of the following is true:
  - non-empty `key_points`
  - non-empty parser-normalized source summary field
  - body content above the minimum non-whitespace threshold defined during planning

If parsing fails, record an explicit failure.

If parsing succeeds but signal is too weak, record an explicit skipped status rather than generating a low-value knowledge note.

### Topic Assignment

Topic assignment is not flat tagging. The model must decide:

- whether the knowledge fits an existing path
- whether a new node is needed
- whether a node should be renamed or merged
- which deepest canonical topic path best represents the note

Ancestor inheritance is automatic once the deepest canonical path is chosen.

### Topic Update Decision

For each touched node:

1. Aggregate all associated knowledge notes for the node
2. Compare the new aggregate against the current topic page
3. Decide whether the summary zone must be updated
4. Insert newly supported details into the detail zone
5. Refresh source links

## Tree Restructuring Behavior

When hierarchy changes, the system must not only update the modified node. It must also update all data affected by the changed structure.

### Trigger Cases

- parent/child move
- node split
- node merge
- node rename
- alias canonicalization

### Required Rebuild Scope

- affected node pages
- ancestor node pages
- descendant node pages
- knowledge note topic paths
- graph lineage metadata
- source-to-topic mappings

The rebuild should be **local to the affected subtree and related mappings**, not a full-library re-run unless explicitly requested.

## Source-to-Topic Mapping Store

### File

`_meta/source-topic-map.json`

### Purpose

Persist the relationship between source inbox notes, generated knowledge notes, and topic nodes so local rebuilds and repairs do not need to infer everything from markdown alone.

### Minimum Record Shape

Each record should include:

- source inbox path
- source content fingerprint/hash
- source processing status (`processed` / `skipped` / `failed` / `tombstoned`)
- knowledge note ID
- knowledge note path (nullable unless status=`processed` or `tombstoned`)
- primary topic node ID (nullable unless status=`processed` or `tombstoned`)
- secondary topic node IDs (empty when not applicable)
- ancestor topic node IDs inherited for aggregation (empty when not applicable)
- graph version used during generation
- last generated timestamp
- persisted first-seen inbox path

### Usage

This mapping store is required for:

- local subtree rebuilds after topic changes
- repair-script diagnosis
- migration bookkeeping
- path rewrites after rename/merge/move

## Topic Detail Index

### File

`_meta/topic-detail-index.json`

### Purpose

Persist per-topic detail fingerprints and supporting evidence so detail-zone updates are idempotent and repairable.

### Minimum Record Shape

- topic node ID
- detail fingerprint
- detail type
- normalized semantic statement
- supporting source inbox paths
- last updated timestamp

### Ownership

Persisted mapping state must be owned by a dedicated metadata-state unit, not spread across unrelated renderers or rebuilders.

The same metadata-state unit also owns:

- `topic-detail-index.json`
- `pending-topic-mutations.json`

## Persistence and Recovery Model

### Single-Writer Rule

All mutating flows must share the same serialization model:

- normal per-note pipeline runs
- repair/apply runs
- migration runs

Only one such write transaction may mutate graph, mapping, run-log, or generated topic/knowledge state at a time.

### Lock Contention Rule

Mutating flows must block on the shared write lock up to a bounded timeout. If the timeout is exceeded, the run fails explicitly and records a lock-contention failure in the run log; it must not proceed in parallel.

### Write Order

Each successful processing run must persist in this order:

1. append run-log start record
2. build proposed graph delta in memory
3. build knowledge note content in memory
4. build topic page deltas in memory
5. write knowledge note files
6. write topic page files
7. write topic detail index snapshot
8. write pending mutation snapshot
9. write topic graph snapshot
10. write source-to-topic mapping snapshot
11. update run log with success record

The graph and mapping snapshots are committed last so they do not point at files that were never written.

### Run Log

Every run must append transaction state into a pipeline run log under `_meta/` so failures are diagnosable and repairable.

Minimum fields:

- run ID
- run scope (`single_note` | `repair` | `migration`)
- source note paths or batch selector
- started / completed timestamps
- files intended
- files written
- graph changed yes/no
- mapping changed yes/no
- final status

### Partial Failure Recovery

If a run fails after some files are written:

- the existing run-log start record is updated to failed status
- graph and source-topic mapping snapshots are not advanced unless both writes succeed
- the repair script must treat failed runs as recovery candidates and reconcile orphaned files or stale mappings

### Source Change / Rename / Deletion Handling

The source fingerprint in `source-topic-map.json` is used to detect source changes.

- if source content changes: retract the prior contribution set for that source, then regenerate the single primary knowledge note and all affected topic aggregates
- if source path renames but fingerprint is unchanged: update mapping path references without treating it as new knowledge
- if source note disappears: mark its mapping tombstoned, remove or relink derived files during repair/apply, and subtract its contributions from affected topic nodes

### Corrupted Metadata Recovery

If `_meta/` state files are missing or corrupted:

- the system must not silently continue with partial assumptions
- the repair workflow must be able to rebuild:
  - topic graph snapshot
  - source-topic mapping snapshot
  - topic detail index
  - pending mutation store (as far as recoverable from surviving evidence)
- unrecoverable metadata loss must be surfaced in the repair report as manual-review-required

## Repair and Diagnosis Script

### File

Add a standalone script dedicated to full-library diagnosis and repair.

### Purpose

Prevent the knowledge base from drifting into inconsistent states after many incremental updates.

### Modes

- `dry-run`: report problems without changing files
- `apply`: repair issues
- `apply` with explicit pending-mutation flag: repair issues and resolve targeted pending mutations

### Minimum Checks

1. topic path broken or missing from graph
2. parent/child relations inconsistent
3. orphan topic nodes
4. duplicate or overlapping canonical nodes
5. renamed/merged nodes leaving stale paths behind
6. knowledge pages whose topic mappings no longer match current graph
7. topic pages missing required sections
8. summary/detail zone conflicts
9. source links missing or malformed

### Minimum Repairs

1. rebuild path metadata
2. relink orphan knowledge notes
3. refresh affected topic pages
4. remove or redirect stale topic paths
5. repair graph lineage
6. surface pending mutations grouped by status and support count
7. apply or reject pending mutations only when explicitly requested
8. emit a repair report describing:
   - issues found
   - actions taken
   - items still requiring manual review

### Auto-Repair Safety Boundaries

Safe to auto-apply:

- rebuild derived topic pages
- rebuild source-topic mappings
- relink notes to an already-canonical active node
- repair parent/child pointers when the canonical graph snapshot is unambiguous
- remove stale generated topic files when they are confirmed orphaned and replaced

Manual-review-only:

- semantic merge/split decisions not already represented by canonical graph state
- deleting a knowledge note with ambiguous source ownership
- applying low-confidence topic moves
- choosing between competing canonical replacements with similar confidence
- accepting or rejecting pending mutations unless repair/apply explicitly targets them

## Prompt Configuration

All new or changed LLM prompts introduced by this redesign must move to configuration.

### Requirement

Prompts must be:

- defined as environment-backed settings in `.env`
- shipped with sensible code defaults
- automatically filled when `.env` leaves them unset

### Prompt Families Covered

At minimum:

1. hierarchical topic path generation
2. knowledge-note distillation
3. topic summary-zone update decision
4. topic summary-zone rewrite
5. topic detail-zone insertion/merge
6. repair-script diagnosis and repair judgment

### Design Constraint

The code must not rely on hardcoded inline prompt strings for these new responsibilities.

## File and Responsibility Breakdown

Expected new or refactored units:

- `app/services/knowledge_pipeline/topic_graph.py`
  - own persisted graph state
  - load/save graph snapshots
  - node CRUD and mutation application
- `app/services/knowledge_pipeline/knowledge_distiller.py`
  - distill inbox content into structured knowledge units
- `app/services/knowledge_pipeline/topic_path_resolver.py`
  - read current graph state
  - propose topic paths and mutation proposals
  - must not persist graph changes directly
- `app/services/knowledge_pipeline/knowledge_note_renderer.py`
  - render distilled knowledge notes
- `app/services/knowledge_pipeline/knowledge_note_store.py`
  - apply knowledge note placement and moves
  - preserve old-path traceability for renamed/rebuilt notes
  - resolve filename collisions using `knowledge_note_id`
- `app/services/knowledge_pipeline/topic_page_renderer.py`
  - pure renderer
  - receives already-decided summary payload + detail payload
  - does not decide whether rewrite vs append should happen
- `app/services/knowledge_pipeline/topic_rebuilder.py`
  - take graph state + source-topic mappings as input
  - recompute affected node pages and mapping deltas
  - must not decide new topic paths for fresh source content
- `scripts/diagnose_knowledge_library.py` (name can be finalized during planning)
  - library-wide diagnosis and repair entry point

### Interface Boundaries

- `topic_path_resolver` outputs:
  - proposed primary topic path
  - proposed secondary topic paths
  - optional graph mutation proposals
- `topic_graph` inputs:
  - mutation proposals
  - current graph snapshot
  - returns committed graph snapshot + applied/deferred mutation result
- `topic_rebuilder` inputs:
  - committed graph snapshot
  - source-topic mapping snapshot
  - impacted node IDs
  - returns rebuilt topic page payloads and mapping corrections
- `knowledge_note_store`
  - inputs:
    - `knowledge_note_id`
    - rendered knowledge note payload
    - primary canonical topic path
    - existing mapping state
  - outputs:
    - final note path
    - prior-path trace metadata
    - move/applied path update result
- `orchestrator`
  - coordinates per-note execution
  - asks `topic_rebuilder` for topic update decisions
  - does not embed summary/detail merge logic inline
- `app/services/knowledge_pipeline/metadata_state.py`
  - owns load/save/validation for:
    - `source-topic-map.json`
    - `topic-detail-index.json`
    - pending mutation store
    - pipeline run log
  - owns shared write lock acquisition for pipeline / repair / migration flows

Existing files likely to change:

- `app/config.py`
- `app/services/knowledge_pipeline/orchestrator.py`
- `app/services/knowledge_pipeline/parser.py`
- `app/services/knowledge_pipeline/category_map.py` (replace or migrate into topic graph responsibility)
- `app/services/knowledge_pipeline/topic_updater.py` (likely split or replaced)
- `app/services/knowledge_pipeline/archiver.py` (replace current raw-copy archive behavior)

## Error Handling

### Generation Failures

- Distillation failure must not silently create raw-copy knowledge notes.
- Topic path generation failure should surface clearly and mark the source as `failed` or `skipped` according to the source-processing status contract.
- Topic restructuring failure should not leave half-written graph state without reportable diagnostics.
- malformed LLM structured output (invalid path shape, unknown node IDs, missing required fields, non-numeric confidence) must be treated as explicit generation failure or deferred-mutation failure, never silently coerced into graph mutation

### Repair Failures

- Repair script must produce explicit issue reports
- unrepaired items must be surfaced, not swallowed
- destructive repairs should be gated behind `apply`

## Migration Strategy

The current flat `category-map` and flat `_topics` notes will not satisfy the new structure.

Migration should therefore support:

1. bootstrap topic graph from current notes where possible
2. regenerate knowledge notes from inbox rather than reusing copied article bodies
3. rebuild topic pages from regenerated knowledge notes
4. preserve source links during migration
5. rebuild `source-topic-map.json` as the canonical mapping layer

### Migration Success Criteria

Migration is complete only when all of the following hold:

1. every eligible inbox note has either:
   - one generated primary knowledge note, or
   - an explicit failure record
2. every knowledge note resolves to an active canonical topic path
3. every topic node has a corresponding topic page
4. no orphan knowledge notes remain outside graph/mapping coverage
5. no stale topic paths remain without redirect or alias coverage
6. repair script reports zero blocking structural errors in dry-run mode

## Testing Strategy

Required coverage areas:

1. hierarchical topic generation from source content
2. ancestor inheritance for deep topics
3. knowledge-note output excludes raw full body copy
4. topic page summary zone updates only when topic understanding changes
5. topic page detail zone inserts genuinely new details without duplication
6. topic restructure triggers subtree-local rebuild
7. repair script detects drift correctly
8. repair script repairs supported anomalies correctly
9. prompt settings load from env and fall back to defaults

## Out of Scope

- replacing inbox as source of truth
- building a general-purpose graph database
- preserving article bodies inside knowledge notes for archival purposes
- full-library rebuild on every single document update

## Open Decisions Deferred to Planning

These are implementation choices, not requirement gaps:

- exact topic page markdown wording/template
- exact JSON schema details for graph versions/hashes
- final script filename for diagnosis/repair
