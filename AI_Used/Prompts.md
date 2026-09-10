# Prompts Used

This document displays the prompts used by the project team during the design, implementation, debugging, testing, and documentation of the **Intelligent Network Configuration Assistant (INA)**.
---

#### By Vedant

## Prompt 1 — Project Architecture, Scope and Technology Selection

> We need to build this project as a group of four. Before writing any code, help us understand
> the problem statement and convert it into a practical software architecture.
>
> First explain exactly what the project is supposed to achieve, what the core workflow should
> look like, and what the minimum viable implementation should contain. Then identify the major
> components/modules required and explain the responsibility of each component and how they
> communicate.
>
> The project should involve a Docker-based network with multiple isolated clients, an
> administrator component, an MCP server, policy/rule-based enforcement, and validation or
> monitoring. Keep the scope realistic for a student project: prioritize a small number of
> clearly working core functions over a large number of incomplete features.
>
> Recommend a suitable programming language, libraries/frameworks, and supporting technologies
> for each component. Explain the important design choices briefly, especially around:
> - network isolation,
> - administrator vs client responsibilities,
> - MCP integration,
> - policy enforcement,
> - auditability,
> - and validation/testing.
>
> Also propose a clean repository/directory structure and explain what should go into each
> directory.
>
> Do not start implementing code yet. The goal of this step is to establish a clear,
> implementable architecture and scope that we can use as the basis for the rest of the project.

## Prompt 2 — Phase 1: Build the Project Skeleton

> We have finalized the architecture and scope of the Intelligent Network Configuration Assistant (INA) project. Now implement **Phase 1: the project skeleton** in the existing repository.
>
> Before making any changes, inspect the repository and understand the current state. Do not overwrite or unnecessarily restructure existing work.
>
> Set up the foundation for the following major components:
>
> - `assistant/` — administrator/assistant-side client responsible for interacting with the system.
> - `mcp_server/` — MCP server exposing the controlled network-management capabilities.
> - `network/` — Docker-based network, client containers, discovery and network configuration.
> - `policy/` — rule-based policy engine and audit logging.
> - `validation/` — validation and monitoring of expected system/network behaviour.
> - `tests/` — automated tests for the individual components.
> - `docs/` — technical documentation for architecture and component behaviour.
>
> Establish clean module boundaries and basic interfaces between these components. The architecture
> should reflect the security model we discussed: the administrator is the control interface,
> clients are isolated network entities, and network/configuration operations should go through
> controlled mechanisms rather than allowing unrestricted client-to-client management.
>
> At this stage, prioritize a **working, minimal foundation** over implementing advanced features.
> Do not add unnecessary functionality just to make the project appear larger.
>
> For each part you create:
> 1. Keep the implementation simple and understandable.
> 2. Follow the agreed architecture.
> 3. Add appropriate initialization/configuration files where required.
> 4. Make the structure easy to extend in later phases.
> 5. Add basic tests or test scaffolding where appropriate.
> 6. Document important architectural decisions.
>
> After implementing the skeleton, inspect the complete repository again and verify that the
> components are internally consistent and that nothing already present has been accidentally
> broken.
>
> Do not consider the task complete merely because the files were created. Run the relevant
> tests/checks and report what was created, what was verified, and anything that still needs
> implementation in later phases.

## Prompt 3 — Implement MCP Tools

> Inspect the current INA repository and implement the MCP server tools according to the existing
> architecture and project requirements.
>
> Before making any changes, inspect the existing codebase, especially the MCP server, network,
> policy, and assistant components, so that the tools integrate with the current implementation
> instead of duplicating or bypassing existing functionality.
>
> Implement a focused set of MCP tools that expose the required network/configuration operations
> through the MCP interface. Each tool should have:
>
> - A clear and descriptive name.
> - A well-defined purpose.
> - Appropriate input parameters and validation.
> - A structured and useful response.
> - Proper error handling for invalid or failed operations.
> - Integration with the existing policy/enforcement mechanisms where applicable.
>
> The MCP server must not become a way to bypass the project's security model. Operations exposed
> through MCP should still respect the project's authorization, policy, and network constraints.
> Do not give clients unrestricted access to administrative operations merely because an MCP tool
> exists.
>
> Keep the implementation minimal and aligned with the project's intended scope. Do not introduce
> unnecessary tools or dependencies.
>
> After implementing the tools:
>
> 1. Inspect the complete MCP implementation for consistency with the existing architecture.
> 2. Check that tool inputs and outputs are handled correctly.
> 3. Verify that invalid inputs and failures are handled safely.
> 4. Run the existing MCP-related tests.
> 5. Add or improve tests for important tool behaviours and edge cases where required.
> 6. Fix any issues discovered during testing.
> 7. Re-run the relevant tests after making fixes.
>
> Finally, summarize the tools implemented, the files changed, how the tools integrate with the
> existing policy/security model, and what was verified.

## Prompt 4 — Security Implementation and Review

> Review the current INA repository with a focus on the project's security model. First inspect the
> existing architecture and implementation across the assistant, MCP server, network, policy, and
> validation components before making any changes.
>
> Identify the security boundaries that the project is expected to enforce, particularly the
> separation between the administrator/control interface and individual clients, controlled access
> to network-management operations, policy enforcement, and prevention of unauthorized operations.
>
> Implement or strengthen the security mechanisms that are appropriate for the current architecture.
> Do not introduce unnecessary security features that are outside the project's scope.
>
> Pay particular attention to:
>
> - Preventing unauthorized access to administrative operations.
> - Ensuring MCP tools cannot bypass the policy layer.
> - Validating requests and tool inputs before performing operations.
> - Maintaining client isolation and enforcing intended communication boundaries.
> - Handling denied operations safely.
> - Recording security-relevant actions through the existing audit mechanism.
> - Avoiding unsafe defaults or accidental privilege escalation.
> - Providing useful error responses without exposing unnecessary internal information.
>
> Before modifying anything, determine whether the existing implementation already handles a
> requirement correctly. Preserve correct behaviour and make targeted changes only where necessary.
>
> After implementation:
>
> 1. Review the security-sensitive code paths end-to-end.
> 2. Check both allowed and explicitly denied operations.
> 3. Test invalid, unauthorized, and edge-case inputs.
> 4. Verify that policy enforcement cannot be bypassed through another component.
> 5. Run the complete relevant test suite.
> 6. Fix any vulnerabilities, incorrect assumptions, or regressions discovered during verification.
> 7. Run the tests again after every significant fix.
>
> Finally, document the security mechanisms that are actually implemented, the threats or misuse
> cases they address, the files/components affected, and the verification performed. Do not claim
> security guarantees that the implementation does not actually provide.

## Prompt 5 — Robust Testing and Verification

> Review the current INA repository and significantly strengthen its testing and verification
> coverage without changing the intended project functionality.
>
> First inspect the complete implementation and the tests that already exist. Understand the
> expected behaviour of each major component before writing new tests. Do not simply increase
> the test count with redundant cases; focus on meaningful functional, integration, negative,
> and edge-case scenarios.
>
> Build a robust testing strategy covering the major project components, including:
>
> - MCP server and MCP tools.
> - Policy engine and rule evaluation.
> - Audit logging.
> - Network/discovery functionality.
> - Validation and monitoring.
> - Assistant/client interactions where applicable.
> - Integration between components.
>
> In addition to normal successful operations, deliberately test failure and misuse scenarios,
> such as:
>
> - Invalid inputs.
> - Missing or malformed parameters.
> - Unauthorized operations.
> - Policy-denied operations.
> - Unsupported operations.
> - Boundary and unusual values.
> - Missing configuration or resources.
> - Repeated operations.
> - Unexpected component failures.
> - Interactions where one component could potentially bypass another component's controls.
>
> Tests should verify actual expected behaviour rather than implementation details wherever
> possible. Keep tests deterministic, isolated, readable, and suitable for repeated execution.
> Avoid modifying production code merely to make a test pass.
>
> Where useful, add fixtures, helper functions, or test utilities to avoid unnecessary duplication.
> Ensure that tests do not depend on the developer's machine-specific state or leave unwanted
> changes behind.
>
> After adding the tests:
>
> 1. Run the complete test suite.
> 2. Investigate every failure rather than simply removing or weakening the failing test.
> 3. Determine whether each failure is caused by the implementation, the test, or an incorrect
>    assumption about the requirements.
> 4. Fix genuine implementation issues where necessary.
> 5. Re-run the complete suite after fixes.
> 6. Check for regressions in previously working functionality.
> 7. Summarize the final test coverage and important behaviours verified.
>
> The goal is not just to achieve a high number of passing tests, but to demonstrate that the
> project behaves correctly under normal, invalid, unauthorized, and edge-case conditions.

## Prompt 6 — Project Documentation

> Review the complete current INA repository and create/update the project's technical documentation
> so that it accurately reflects the implementation that actually exists.
>
> Before writing documentation, inspect the source code, configuration files, tests, and existing
> documentation. Do not assume that the documented design and the current implementation are
> identical; use the implementation as the source of truth and update outdated information where
> necessary.
>
> Organize the documentation around the major components and concepts of the project, including:
>
> - Overall architecture and component interaction.
> - MCP server and the tools it exposes.
> - Network architecture and client isolation.
> - Policy engine and rule-based enforcement.
> - Security mechanisms and trust boundaries.
> - Audit logging and governance.
> - Validation and monitoring.
> - Testing strategy and verification.
> - Setup, configuration, and execution instructions.
> - Known limitations, edge cases, and possible future improvements.
>
> For each component, explain its purpose, important implementation details, how it interacts with
> other components, and any relevant design decisions. Keep the documentation understandable to
> someone evaluating or setting up the project for the first time.
>
> Include practical commands and examples where they are required to understand or run the
> project, but ensure that every command matches the current repository structure and implementation.
>
> Do not document features that are not actually implemented. Clearly distinguish implemented
> functionality from limitations or possible future improvements.
>
> Preserve useful existing documentation instead of unnecessarily rewriting everything. Where
> documentation already exists, edit it to correct inaccuracies or improve clarity rather than
> creating duplicate documents.
>
> After completing the documentation:
>
> 1. Cross-check every document against the current source code.
> 2. Verify that file paths, commands, component names, and configuration details are correct.
> 3. Ensure the documentation is consistent across all files.
> 4. Check that a new user can understand the architecture and reproduce the project using the
>    documented setup instructions.
> 5. Report which documentation files were created or modified and what each covers.

## Prompt 7 — Iterative Debugging and Fix Verification

> The implementation has been developed incrementally, so now review the current behaviour for
> errors, inconsistencies, and missing requirements.
>
> First inspect the relevant existing code and understand the intended behaviour before making
> changes. Do not immediately patch the visible error without identifying its root cause.
>
> For every issue found:
>
> 1. Identify the root cause.
> 2. Explain why the current implementation produces the observed behaviour.
> 3. Make the smallest appropriate fix while preserving the existing architecture.
> 4. Review the surrounding code for related issues or regressions.
> 5. Run the relevant tests and verification commands.
> 6. If the fix introduces another issue, investigate and correct it rather than weakening the
>    test or removing the affected functionality.
>
> Pay particular attention to interactions between the MCP tools, policy enforcement, network
> operations, validation, audit logging, and the assistant/client layer. A component working
> independently is not sufficient if its integration with another component is incorrect.
>
> Do not consider an issue resolved merely because the original error disappears. Verify that the
> expected behaviour is now correct and that previously working functionality continues to work.
>
> Keep a clear record of the changes made during the debugging process, including the problem,
> root cause, fix, and verification performed.

---

#### By Khushi

## Prompt 1 - Understanding the Project and My Component

> I am part of a four-person team building the Intelligent Network Configuration Assistant (INA)
> project. Before I start working on my part, help me understand the project as a whole in plain,
> simple language: what problem it solves, what the overall pipeline looks like from a user typing
> a command to the network actually changing, and what each of the four components (assistant, MCP
> server, policy/rules, validation) is responsible for.
>
> My specific ownership is the Policy and Rules component: a rulebook that defines what actions are
> allowed on the network, a policy engine that checks every requested action against that rulebook
> before it reaches the network tools, and an audit log that records every decision made.
>
> Explain this to me from scratch, assuming I don't already know networking or MCP concepts in
> depth. Use plain language and concrete examples rather than jumping straight into architecture
> diagrams or code. Once I understand the full picture, help me understand exactly what my
> component needs to produce and how it fits between the assistant layer and the MCP server layer.

## Prompt 2 - Designing the Rulebook and Policy Engine

> Based on my team's agreed architecture and the real Docker network setup (clients named client1,
> client2, and a protected server container, each with fixed IP addresses), help me design and
> implement the Policy and Rules component.
>
> I need:
> - A YAML rulebook defining protected clients, an allowed bandwidth range, and the set of actions
>   the system is permitted to perform at all.
> - A policy engine exposing a single function that takes an action name and a parameters
>   dictionary, checks it against the rulebook, and returns a clear ALLOW/DENY decision along with
>   a human-readable reason.
>
> The function needs to reject requests for actions outside the allowed list, requests targeting
> unrecognized or protected clients, and bandwidth requests outside the configured minimum/maximum
> range. Keep the implementation simple, in plain Python with light comments explaining what each
> part does, since I want to be able to explain this code to my team and in my report.
>
> After implementing it, test it standalone with a range of realistic inputs - allowed requests,
> denied requests for protected clients, and invalid/unknown clients or actions - so I can confirm
> the logic is correct before anyone else's code depends on it.

## Prompt 3 - Building the Audit Log

> Extend my Policy and Rules component with an audit logging module. Every decision made by the
> policy engine (allowed or denied) should be recorded with a timestamp, the action requested, the
> parameters involved, the resulting status, and the reason behind the decision.
>
> The log should be appendable (new entries added without rewriting the whole file), readable back
> as a full history, and able to produce a plain-English explanation of the most recent decision -
> since my team wants the assistant to eventually be able to answer "why did you do that?" using
> this log as the source of truth.
>
> Keep this module simple and dependency-light, using only what's necessary to read/write structured
> log entries. Test it standalone to confirm entries are written and read back correctly, and that
> the plain-English explanation function produces a sensible sentence from a logged entry.

## Prompt 4 - Integrating with the Real MCP Contract

> My teammate Vedant has shared the actual JSON contract his MCP tools use for block_client,
> unblock_client, and limit_bandwidth, along with the real Docker network configuration (container
> names, IP addresses, and how his tools execute network commands). Review this against my existing
> policy engine and rulebook, and identify any mismatches - for example, client names that don't
> match the real network, or assumptions about the response format that don't match his actual
> tools.
>
> Update my rulebook and policy engine so they are consistent with the real system rather than
> placeholder values. Also help me understand exactly where in the pipeline my policy check needs
> to be called - i.e., before which operations in the MCP server - so that no network change can
> happen without first passing through my policy engine.
>
> Clearly explain what changed and why, so I can communicate the update accurately to my team rather
> than just accepting a change without understanding its reasoning.

## Prompt 5 - Writing Test Cases

> Write a test suite for my policy engine covering both the expected/normal cases and the important
> edge cases: a protected client being denied, an unknown/unrecognized client being denied, bandwidth
> requests above and below the allowed range being denied, a disallowed action being denied outright,
> and the corresponding cases where a request should be correctly allowed.
>
> Keep the tests deterministic and independent of any external system (Docker, network state, or
> other teammates' code), since this component should be fully verifiable on its own. Run the full
> test suite and confirm every case passes, and explain clearly what each test is actually verifying
> so I can describe this testing approach in my project report.

## Prompt 6 - Documenting My Component

> Write clear documentation for my Policy and Rules component, structured similarly to the
> documentation style my teammate used for the MCP server component, so our project documentation
> stays consistent across the team.
>
> The documentation should explain what the component is responsible for, the files it consists of,
> how the rulebook is structured, how other teammates are expected to call my policy engine, how the
> logging works, how to run the tests, and where exactly this component sits in the overall request
> pipeline - from the user's command down to the real network change and back.
>
> Keep the explanation accurate to what is actually implemented, avoid describing anything as
> working that hasn't been verified, and write it so that a teammate or an evaluator unfamiliar with
> my part of the code could understand and verify it without needing to ask me directly.
