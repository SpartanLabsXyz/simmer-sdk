# Contributing to Simmer SDK

## What belongs here

This repo is the **Python SDK and official skills** for Simmer. Contributions welcome in these areas:

- Bug fixes in `SimmerClient` or any SDK method
- New convenience methods or improved error handling
- Type hint improvements
- Documentation fixes

**Skills belong on ClawHub, not here.** If you've built a trading strategy, publish it at [clawhub.ai](https://clawhub.ai) — it will automatically appear in the Simmer registry. See [simmer.markets/skillregistry.md](https://simmer.markets/skillregistry.md).

**For new features or API changes**, open an issue first so we can align before you write code.

## AI-assisted PRs welcome

Built this with Claude, Codex, or another AI tool? Great — just note it in the PR description. We care that the code is correct and you understand what it does, not how it was written.

## Dev setup

```bash
git clone https://github.com/SpartanLabsXyz/simmer-sdk
cd simmer-sdk
pip install -e .
```

Verify your change works against a real agent:

```bash
export SIMMER_API_KEY=sk_live_...
python -c "from simmer_sdk import SimmerClient; c = SimmerClient(api_key='$SIMMER_API_KEY'); print(c.get_markets(limit=1))"
```

## Before opening a PR

- Test your change against a real Simmer agent if possible
- Keep PRs focused — one bug fix or one feature per PR
- Don't mix unrelated changes
- No secrets, API keys, internal thresholds, or proprietary logic — the SDK is public

## PR checklist

Your PR description should briefly cover:

- What problem does this fix or what does it add?
- How did you test it?
- Is it AI-assisted? (just note it)

## Reporting bugs

Open an issue with:
- What you called and what you expected
- The full error or unexpected response
- Your `simmer-sdk` version (`pip show simmer-sdk`)
