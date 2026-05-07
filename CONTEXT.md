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

| Tool | When to use |
|---|---|
| `describe_spec` | First call in any XML-based workflow; returns available specification names, workspace IDs, and dataset lists |
| `search_spec` | Browse and filter conditions in the loaded spec by keyword |
| `run_xml_verification` | Run a named QualitySpecification from the loaded spec, with workspace path substitutions |
| `list_conditions`, `describe_condition` | Explore the full condition catalog for ad-hoc use |
| `run_verification` | Ad-hoc verification without a spec |
