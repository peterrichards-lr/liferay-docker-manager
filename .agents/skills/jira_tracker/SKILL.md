---
name: jira-tracker
description: Guides agents on documenting, naming, and categorizing upstream bugs/limitations in the repository's JIRA issue tracker.
---

# JIRA Issue Tracker & Reporter Skill

This skill guides AI agents on how to document, organize, and track upstream platform limitations, core system bugs, or necessary API extensions inside this repository's `jira/` issue tracker.

## Lifecycle Subdirectories

All bug reports and feature requests must live inside one of these subdirectories under the `jira/` root:

- `jira/todo/`: Draft tickets that have been written/documented but not yet raised on the JIRA platform.
- `jira/open/`: Tickets that are active and currently open on the JIRA platform.
- `jira/closed/`: Tickets that have been resolved, closed, or discarded.

## Naming Conventions

- **In `jira/todo/`**: Use the pattern `LPS-DRAFT-[DESCRIPTION].md` or a descriptive name.
- **In `jira/open/` or `jira/closed/`**: Use the pattern `[JIRA-KEY]-[DESCRIPTION].md` (e.g. `LPD-95079-ACCOUNTS-BATCH-UPSERT.md`).

## Standard Markdown Template

Every issue file must use the following template to guarantee high-quality, reproducible bug reports:

````markdown
# Liferay DXP Bug Report: [Short, Descriptive Title]

[JIRA-KEY] - https://liferay.atlassian.net/browse/[JIRA-KEY]

## Component

- **[Component Name, e.g., Headless Commerce]**
- **[Underlying Engine, e.g., Vulcan Batch Engine]**

## Environment

- **Liferay Product Version**: [e.g., Liferay DXP 2026.q1.7-lts]
- **API Endpoint**: [e.g., /o/headless-admin-user/v1.0/accounts/batch]

## Summary

[A concise overview of the limitation or bug, the context of occurrence, and its impact on development.]

## Description & Technical Analysis

[Detailed analysis of the system behavior, underlying exceptions, database query parameters, or class inheritance issues.]

## Steps to Reproduce

1. [Step 1]
2. [Step 2]
3. [Step 3 with code snippets or payloads]

```json
{
  "example": "payload"
}
```

## Expected Results

[Expected correct behavior of the API or system.]

## Workaround

[The precise implementation or configuration workaround deployed in this codebase to bypass the issue.]
````

## Agent Action Guidelines

When you discover an upstream bug or platform limitation:

1. **Create Draft**: Write a draft bug report following the template and save it to `jira/todo/`.
2. **Implement Workaround**: Implement the necessary resilient logic or configuration workaround in the codebase, documenting it in the file.
3. **Register JIRA Key**: When the issue is raised on JIRA, use `git mv` to rename the file to include the JIRA Key and move it to `jira/open/`. Update the JIRA link inside the file.
4. **Audit Statuses**: Periodically audit the open tickets. If a ticket has been resolved or closed on JIRA, move it to `jira/closed/`.
