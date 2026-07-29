# ProSuite MCP: Domain Context

## Core Concepts

**QualitySpecification**
A named collection of QualityConditions defined inside a `.qa.xml` spec file. A single file can contain multiple QualitySpecifications. Domain experts author and own these files; they represent the authoritative source of what should be checked and how.

**QualityCondition**
A configured test: a test class/factory name (`testDescriptor`) plus parameter values, including per-condition dataset filters (WHERE clauses on specific datasets). The human-readable `name` attribute (often in German) is the domain knowledge; the `testDescriptor` is the implementation detail. The same physical dataset can be referenced with different filters in different conditions within the same spec.

**Workspace**
A data source defined in the XML by a logical identifier (e.g. `DATA_OSM`). At run time the logical ID is replaced with an actual workspace path on the ProSuite server (`.sde` connection file or file geodatabase path).

**WorkspaceReplacement**
A mapping from `workspace_id` to `workspace_path`, provided at run time to `run_xml_verification`. Required for every workspace ID referenced by the selected QualitySpecification.

**Per-condition dataset filter**
A WHERE clause (`where` attribute on a `<Dataset>` parameter) that is part of a specific QualityCondition's configuration, not a global property of the dataset. Example: `Natur where subtype=0` in one condition, `Natur` (no filter) in another. This is structurally different from the MCP's flat dataset list, which is why the `search_spec` to `run_verification` flow loses filters.

## Verification Modes

**Spec-as-is mode** (`run_xml_verification`)
Sends the XML spec to the ProSuite gRPC service as a string. The service interprets it natively. Per-condition filters, default scalar values, transformer dependencies, and all other spec details are preserved exactly as configured. Preferred for all scenarios where a `.qa.xml` spec exists.

**Ad-hoc mode** (`run_verification`)
Builds a `Specification` programmatically from individual `ConditionRequest` objects. Useful when no spec exists and the LLM constructs conditions from scratch using `list_conditions` and `describe_condition`. Prone to LLM semantic errors (picking wrong conditions, wrong parameters) and structurally cannot represent per-condition dataset filters.

## Tool Roles

| Tool                                    | When to use                                                                                                                    |
| --------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| `describe_spec`                         | First call in any XML-based workflow; returns available specification names, workspace IDs, and dataset lists                  |
| `search_spec`                           | Browse and filter conditions in the loaded spec by keyword                                                                     |
| `run_xml_verification`                  | Run a named QualitySpecification from the loaded spec, with workspace path substitutions                                       |
| `list_conditions`, `describe_condition` | Explore the full condition catalog for ad-hoc use                                                                              |
| `run_verification`                      | Ad-hoc verification without a spec                                                                                             |
| `condition_to_xml`                      | Preview a single condition's XML in isolation; requires an already-known `test_descriptor` alias                               |
| `add_condition_to_spec`                 | Preview adding a new QualityCondition to a spec, reusing an existing descriptor; returns updated spec XML, never writes a file |
| `preview_condition_run`                 | Tier-2 authoring check: run a proposed condition ad-hoc and see what it actually flags, before merging it into a spec          |

## Spec Authoring

`condition_to_xml` and `add_condition_to_spec` are preview-only: they build the condition through the same authoritative prosuite factory `run_verification` uses, so parameter names and value formatting are engine-derived, not guessed. Neither tool writes to disk — the caller (human or agent) reviews the returned XML and decides separately whether and how to persist it. `add_condition_to_spec` only reuses an existing `<TestDescriptor>`; it never synthesizes one, so a condition whose test type isn't already referenced somewhere in the target spec cannot currently be added this way.

`preview_condition_run` closes the gap between Tier-1 (builds, references a valid descriptor) and Tier-2 (actually runs and flags real features): it's a thin wrapper around `run_verification` for a single condition, so the caller sees the same engine-confirmed summary — including actual flagged issues, not just bind success — before deciding to merge a proposed condition into a spec.
