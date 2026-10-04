---
name: team-knowledge
description: Record a hard-won team finding as a reviewed merge request, or look up what the team already knows about a project, system, environment, desk, or tool. User-invoked; never runs on its own. Use when the user says "record this", "note that X doesn't work", "/team-knowledge", "what do we know about", "anything known about", or asks whether an approach has been tried before.
---

# Team knowledge

The `team-knowledge` MCP server holds the team's structured findings. Findings are short, schema-validated
files scoped by entities. New findings go through a merge request that a colleague approves, so a draft
should read the way a careful colleague would want to read it. Nothing in this skill runs unless the user asks.

## Ask mode

Trigger: `/team-knowledge ask <question or scope>`, or a plain question like "what do we know about the UAT gateway".

1. Resolve scope. Turn the systems, projects, environments, desks, and tools the user names into entity refs
   with `list_entities` (fuzzy, matches aliases). If the current repo has a `.team-knowledge.yaml` with a
   `scope:` list, treat those refs as the default scope when the user gives none.
2. For a scoped question call `findings_for_scope` with those refs. For an open question call `search_findings`
   with the user's words, passing any resolved refs as `scope` so they boost. Use both when unsure.
3. Present a short list: kind, title, claim, author and date, wiki link. Lead with dead-ends and caveats when
   the user is about to attempt something. Say plainly when nothing was found. Never invent a finding.
4. Offer `get_finding` for detail only if the user wants it.

Encourage, but never force, an Ask at the start of work on a scoped system.

## Record mode

Trigger: `/team-knowledge record`, or "record that", "note this for the team", "that should go in the knowledge base".

1. Gather what was learned from the session: the approach, what happened, the evidence (errors seen, merge
   requests, Confluence pages, observations with dates), and where it applies.
2. Choose the kind. `dead-end` for an approach that does not work here. `caveat` for behaviour that surprises
   and causes mistakes. `how-to` for a recipe that works. `decision` for a choice and its rationale. `fact` for a
   stable property of a system or environment.
3. Resolve scope with `list_entities`. If an entity is genuinely new, include it in `new_entities` with type,
   slug, name, and a one-paragraph description. Ask the user only when a ref is genuinely ambiguous.
4. Draft the header and sections. Title: one specific declarative sentence, 10 to 120 characters. Claim: the
   finding in one to three sentences, specific enough to be wrong. Evidence: at least one item; observations
   carry a dated note. Confidence: `observed` if the user saw it, `inferred` if deduced, `reported` if told.
   Sections: only the kind's headings, in order, under 400 words total.
5. Show a compact preview: kind, title, scope, claim. Ask for one confirmation. Then call `propose_finding`.
6. Report the merge request URL and the reviewers. If the tool returns `errors`, fix what the suggestions make
   obvious and ask the user only for what you cannot resolve. If it returns `error` with a `retry` command,
   tell the user the finding is committed locally and give them the command.

Superseding an existing finding is `propose_finding` with `supersedes: [old id]`. Correcting one is
`amend_finding`. Withdrawing one is `retract_finding` with a reason.

## Quality bar

- Scope precisely. A finding about the UAT gateway is scoped to `system/kdb-gateway` and `environment/uat`, not to the project that happened to hit it.
- Claims are falsifiable. "The gateway is slow" is not a finding; "selects over 10k symbols exceed the 30s UAT gateway timeout" is.
- Do not pad. Required sections only, no preamble, no sign-off.
- Never record secrets, credentials, or client data.

## Tool reference

| Tool | Use |
|---|---|
| `list_entities(type?, query?)` | resolve scope refs; see owners and active counts |
| `findings_for_scope(scope, kinds?, include_related?)` | deterministic lookup by entity |
| `search_findings(query, scope?, kinds?)` | hybrid search; `warnings` says if semantic search was unavailable |
| `get_finding(finding_id)` | full detail |
| `propose_finding(...)` | open a merge request for a new finding |
| `amend_finding(finding_id, reason, header_changes?, sections?)` | correct an accepted finding |
| `retract_finding(finding_id, reason)` | withdraw a finding |
| `my_proposals()` | the user's open merge requests |
