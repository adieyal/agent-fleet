# Directory structure for Fleet business modules

**Status:** Proposed target layout for [ADR 0008](../adr/0008-business-ownership-boundaries.md). It applies the business-module skill to Fleet's existing `fleet` package. This is a design, not a file move.

## Choose modules by business responsibility

The directory name must answer “what business capability owns this behavior?” A module needs its own vocabulary, invariants, workflow, and callers. A table, transport, projection, or process is insufficient reason to create one. The [ownership map](business-module-ownership.md#ownership-map) identifies candidate owners; create each package only when its first real behavior is implemented.

| Boundary | Reason it earns a module |
| --- | --- |
| Workspace | Project identity, live capacity, floor assignment and shutter transitions change together. |
| Work | Work items, criteria, waiting conditions and accepted progress share completion rules. |
| Execution | Actions, claims, run attempts, delivery and reconciliation share execution identity and lifecycle. |
| Authority | Role activations and mandate-version authority determine who may make a change. |
| Decisions | Requests, proposals and immutable answers have their own acceptance lifecycle. |
| Attention | Deduplication, ownership, acknowledgement and resolution apply across work and sources. |
| Library | Canonical document references, availability and indexing rules form one cataloguing capability. |
| Observations | Source, timestamp, freshness and retained readings define whether a value is current. |
| Records | Authorship, canonical revision and provenance govern Fleet-owned documents. |

Attention and Decisions may start as one package if their lifecycles are implemented together; split them only when their policies and callers diverge. Records and Observations likewise need business behavior before they become packages. A pending module name does not justify an empty scaffold.

## Target layout

```text
pyproject.toml                         # non-built uv workspace and root dev tools
packages/
├── fleet/src/fleet/
│   ├── container.py                   # dependency-injector; owns adapter construction
│   ├── modules/                       # public facades, application ports and domain
│   ├── infrastructure/                # SQLite, configuration, Git and documents
│   ├── services/                      # controller workflows
│   ├── projections/                   # assembled read models
│   ├── ingestion.py                   # applies worker observations
│   ├── transport.py                   # worker calls, streams, shell and rsync
│   └── remote/fleetd.py               # standalone stdlib-only worker
├── fleet-cli/src/fleet_cli/
│   ├── cli.py                         # installed fleet console script
│   └── plugins.py                     # fleet.commands distribution metadata loader
└── fleet-web/src/fleet_web/
    ├── entrypoint.py                  # standalone fleet-web and web plugin
    ├── server.py                      # HTTP/SSE presentation
    ├── documents.py                   # Markdown response rendering
    ├── fixture.py                     # fixture presentation
    └── static/                        # index, CSS, JS, assets, vendor and prototypes

tests/
├── modules/<name>/                    # domain/application tests with fake ports
├── integration/                       # SQLite, Git, worker and document adapters
├── projections/                       # assembled read-contract tests
├── container/                         # provider overrides and unit bindings
└── packaging/                         # installed distributions and plugin contract
```

The directory `packages/fleet/src/fleet/modules/` substitutes for the skill's `backend/modules/` because this project installs `fleet` as its Python package. `packages/fleet/src/fleet/infrastructure/` is the corresponding external adapter root. `fleet.container` performs dependency injection only. SQLite connection handling, migrations, transaction scope and change-log writes live in `infrastructure/sqlite/`; there is no separate controller package containing business behavior. The controller remains the in-process composition of modules and adapters.

Projections may assemble already computed module results, group markers for display, and format read documents. Work computes accepted progress, Observations computes freshness, and Attention determines which items are open. A projection cannot create a second completion or freshness policy. The CLI and web server use module facades for commands and projections for reads.

## Required shape of an implemented module

Execution is the example. The names below show the skill's layer boundaries; create individual files only when they contain real behavior.

```text
packages/fleet/src/fleet/modules/execution/
├── __init__.py                    # supported public import surface
├── facade.py                      # sole behavioral entrypoint for callers
├── domain/
│   ├── __init__.py
│   ├── entities.py                # Action, Claim, Run transitions
│   ├── value_objects.py           # run/action identities and statuses
│   ├── policies.py                # claim and reconciliation invariants
│   └── exceptions.py
└── application/
    ├── __init__.py
    ├── dtos.py                    # typed boundary results and records
    ├── inputs.py                  # strict parsing; no business decisions
    ├── ports.py                   # mechanical storage/transport Protocols
    └── use_cases/
        ├── __init__.py
        ├── dispatch_action.py
        ├── reconcile_run.py
        └── deliver_input.py
```

`__init__.py` exports `ExecutionFacade`, the support DTOs and ports needed by callers and wiring, and module exceptions. It does not export use cases, parsing helpers or private domain types. Facade methods accept caller-friendly inputs and delegate to use cases. Domain code enforces legal transitions with standard-library types. Application code owns workflow order and explicit update semantics. Its ports expose mechanical operations such as `get_run`, `save_run`, and `send_create`, not `decide_failure` or `choose_host`. Adapters implement those ports without policy. Known shapes cross the public boundary as typed DTOs, not nested `dict[str, Any]` values.

Within a module, imports are relative. An external caller imports from `fleet.modules.execution`, never `fleet.modules.execution.application...`. Modules never import `fleet.infrastructure`, `fleet_web`, or `fleet_cli`. Production adapters are constructed in `fleet.container` and injected through facade constructors. A module can therefore be tested using fake ports.

## A cross-module command

Dispatch belongs to Execution because it creates an action claim and run intent. Its application use case calls public Authority, Workspace and Work facades for their respective decisions: whether the actor is authorised, whether the project accepts a claim, and whether the work target exists. These collaborators are injected at composition time and called through their public contracts; Execution does not read their private tables or recreate their policies. The shared SQLite unit of work makes the checks and claim atomic. Only the Execution facade is exposed as the dispatch entrypoint to CLI and web.

The dispatch use case records intent before the SSH adapter sends a worker command. A lost SSH response leaves an uncertain run to reconcile; it does not repeat the claim. The worker protocol remains a versioned wire contract so standalone `fleetd.py` never imports controller modules.

## Migration from the current files

| Current file or area | Destination as behavior moves |
| --- | --- |
| `fleet/projects.py`, `fleet/workspace.py`, `fleet/building.py` | Workspace domain/application; JSON import and SQLite writes become infrastructure adapters. |
| `fleet/attention.py` | Attention policy and use cases; visual marker assembly goes to projections. |
| `packages/fleet-cli/src/fleet_cli/cli.py` | Retain entrypoint; remove business decisions after facade commands exist. |
| `fleet/transport.py` | SSH and config adapters; transport exceptions translate at the adapter boundary. |
| `packages/fleet-web/src/fleet_web/live.py`, state logic in `packages/fleet-web/src/fleet_web/server.py` | Module-owned state and projections; stream handling goes to `web/ingester.py`. |
| `packages/fleet-web/src/fleet_web/library.py`, `packages/fleet-web/src/fleet_web/documents.py` | Library indexing, constrained document-read adapter, and web Markdown renderer respectively. |
| `packages/fleet-web/src/fleet_web/fixture.py` | Demo/test adapter outside business modules. |
| `packages/fleet/src/fleet/remote/fleetd.py` | Preserve one standalone file and its installation path. |

Start with the first [workspace-plan](first-working-workspace-plan.md) increment: implement Workspace's domain, use cases, facade and SQLite port, wire both CLI and web to that facade, then retire the corresponding JSON writes. Add other modules when their records and commands arrive. Do not retain active forwarding aliases in old files once callers have moved.
