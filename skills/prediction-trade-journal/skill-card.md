## Description:

Auto-log trades with context, track outcomes, generate calibration reports to improve trading.

This skill is ready for commercial/non-commercial use.

## Publisher:

[simmer](https://clawhub.ai/user/simmer)

### License/Terms of Use:

MIT-0

## Use Case:

External users and developers use this skill to sync Simmer prediction-market trades, maintain a local trade journal, review outcomes, and generate trading performance reports.

### Deployment Geography for Use:

Global

## Known Risks and Mitigations:

Risk: SIMMER_API_KEY may be sent to an environment-selected API host if SIMMER_API_URL is controlled by untrusted configuration.

Mitigation: Use the skill only where SIMMER_API_URL cannot be set by untrusted configuration, or remove or lock that override to the official Simmer API host before using the API key.

Risk: The artifact declares an unused, unpinned simmer-sdk dependency.

Mitigation: Remove the unused dependency or pin it to a reviewed version before installing in managed environments.

Risk: Local journal files and CSV exports can contain sensitive financial trading records.

Mitigation: Treat data/trades.json, data/context.json, and exported CSVs as sensitive records; restrict access and avoid committing or sharing them.

## Reference(s):

- [Prediction Trade Journal ClawHub listing](https://clawhub.ai/simmer/skills/prediction-trade-journal)
- [Simmer API base URL](https://api.simmer.markets)

## Skill Output:

**Output Type(s):** [text, markdown, code, shell commands, configuration, guidance]

**Output Format:** [Markdown guidance with shell commands, Python integration snippets, terminal reports, JSON trade storage, and CSV exports.]

**Output Parameters:** [1D]

**Other Properties Related to Output:** [Requires SIMMER_API_KEY for API-backed sync and stores trade records locally.]

## Skill Version(s):

1.1.13 (source: server release evidence; artifact frontmatter reports 1.1.8)

## Ethical Considerations:

Users should evaluate whether this skill is appropriate for their environment, review any generated or modified files before relying on them, and apply their organization's safety, security, and compliance requirements before deployment.
