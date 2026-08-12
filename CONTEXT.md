# ProSuite MCP: Domain Context

## Core Concepts

**QualitySpecification**
A named collection of QualityConditions defined inside a `.qa.xml` spec file. A single file can contain multiple QualitySpecifications. Domain experts author and own these files; they represent the authoritative source of what should be checked and how.

**QualityCondition**
A configured test: a test class/factory name (`testDescriptor`) plus parameter values, including per-condition dataset filters (WHERE clauses on specific datasets). The human-readable `name` attribute (often in German) is the domain knowledge; the `testDescriptor` is the implementation detail. The same physical dataset can be referenced with different filters in different conditions within the same spec.

**Workspace**
A data source defined in the XML by a logical identifier (e.g. `DATA_OSM`). At run time the logical ID is replaced with an actual workspace path on the ProSuite server (`.sde` connection file or file geodatabase path).

**WorkspaceReplacement**
A mapping from `workspace_id` to `workspace_path`, provided at run time to `start_xml_verification` (or its synchronous compatibility counterpart). Required for every workspace ID referenced by the selected QualitySpecification.

**Per-condition dataset filter**
A WHERE clause (`where` attribute on a `<Dataset>` parameter) that is part of a specific QualityCondition's configuration, not a global property of the dataset. Example: `Natur where subtype=0` in one condition, `Natur` (no filter) in another. This is structurally different from the MCP's flat dataset list, which is why the `search_spec` to `run_verification` flow loses filters.

## Verification Modes

**Spec-as-is mode** (`start_xml_verification`)
Sends the XML spec to the ProSuite gRPC service as a string. The service interprets it natively. Per-condition filters, default scalar values, transformer dependencies, and all other spec details are preserved exactly as configured. Preferred for all scenarios where a `.qa.xml` spec exists.

**Ad-hoc mode** (`start_verification`)
Builds a `Specification` programmatically from individual `ConditionRequest` objects. Useful when no spec exists and the LLM constructs conditions from scratch using `list_conditions` and `describe_condition`. Prone to LLM semantic errors (picking wrong conditions, wrong parameters) and structurally cannot represent per-condition dataset filters.

## Issue Results

**Violation vs. unevaluable condition**
The stream reports both as issues with `allowable=False`, distinguished only by `issue_code`. A violation carries a code naming the test (e.g. `Constraints.ConstraintNotFulfilled`) with the failing expression as its description. A condition the engine could not evaluate carries an empty code and a description beginning `Error testing`, for instance a constraint referencing a field the table does not have. ProSuite reports the latter per row and keeps going rather than aborting, which is deliberate: expression parameters are customer-specific and one misconfigured condition must not kill a long verification. Anything tallying issues without separating the two counts a broken condition as data errors.

## Tool Roles

| Tool                                    | When to use                                                                                                                        |
| --------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| `describe_spec`                         | First call in any XML-based workflow; returns available specification names, workspace IDs, and dataset lists                      |
| `search_spec`                           | Browse and filter conditions in the loaded spec by keyword                                                                         |
| `start_xml_verification`                | Queue a named QualitySpecification from the loaded spec and return a run ID                                                        |
| `list_conditions`, `describe_condition` | Explore the full condition catalog for ad-hoc use                                                                                  |
| `start_verification`                    | Queue an ad-hoc verification without a spec                                                                                        |
| `get_verification_status`               | Poll elapsed time, latest ProSuite message, progress, and output location                                                          |
| `get_verification_result`               | Retrieve the persisted result after a run reaches a terminal state                                                                 |
| `add_condition_to_spec`                 | Preview adding a new QualityCondition to a spec, reusing an existing descriptor; returns the updated spec XML, never writes a file |
| `preview_condition_run`                 | Tier-2 authoring check: run a proposed condition ad-hoc and see what it actually flags, before merging it into a spec              |

## Spec Authoring

`add_condition_to_spec` is preview-only: it builds the condition through the same authoritative prosuite factory `run_verification` uses, so parameter names and value formatting are engine-derived, not guessed. It never writes to disk; the caller (human or agent) reviews the returned XML and decides separately whether and how to persist it. It only reuses an existing `<TestDescriptor>`, never synthesizing one, so a condition whose test type isn't already referenced somewhere in the target spec cannot currently be added this way.

`preview_condition_run` closes the gap between Tier-1 (builds, references a valid descriptor) and Tier-2 (actually runs and flags real features): it's a thin wrapper around `run_verification` for a single condition, so the caller sees the same engine-confirmed summary, including actual flagged issues rather than just bind success, before deciding to merge a proposed condition into a spec.
