# AI PR Code Reviewer Action

An automated Pull Request code reviewer powered by OpenRouter language models (like Gemini, Llama) or the Anthropic API (Claude) directly. Submits both high-level summaries and line-by-line inline review comments directly to GitHub PRs.

## Features
- Two providers: OpenRouter (dynamic model swapping — Gemini, Llama, etc.) or Anthropic (Claude, via an Anthropic API key or a Claude Pro/Max subscription OAuth token).
- Line-by-line inline code comments on modified lines only.
- Strict bug, performance, readability, and security focus.
- Lightweight, plain Python implementation with zero third-party library dependencies.

## Usage

### Public Repositories (or Organization Shared Private Repositories)
If this action is shared within your GitHub Organization, you can reference it directly:

```yaml
name: Automated Code Review
on:
  pull_request:
    types: [opened, ready_for_review]

permissions:
  pull-requests: write
  contents: read

jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout Code
        uses: actions/checkout@v4

      - name: Run Review Action
        uses: menil/pr-code-review-action@main
        with:
          openrouter_api_key: ${{ secrets.OPENROUTER_API_KEY }}
```

> [!TIP]
> Pin to a specific commit SHA rather than `@main` for stability.

### Using Anthropic (Claude) instead of OpenRouter
Set `provider: anthropic` and supply credentials using **one** of the two methods below. OpenRouter is not involved in either case.

**Anthropic API key** — billed per token against that key:

```yaml
      - name: Run Review Action
        uses: menil/pr-code-review-action@main
        with:
          provider: anthropic
          anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
          anthropic_model: claude-haiku-4-5   # optional; this is the default
```

**Claude Pro/Max subscription (OAuth token)** — uses your subscription's usage allowance instead of pay-per-token billing. Generate a long-lived token locally with `claude setup-token` and store it as a repository secret:

```yaml
      - name: Run Review Action
        uses: menil/pr-code-review-action@main
        with:
          provider: anthropic
          claude_code_oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
          anthropic_model: claude-haiku-4-5   # optional; this is the default
```

> [!NOTE]
> Provide credentials for whichever provider you select. If both `anthropic_api_key` and `claude_code_oauth_token` are set, `claude_code_oauth_token` takes precedence — a configured Claude Code subscription is preferred over a billed API key. On the Opus/Sonnet-5 tiers the action deliberately omits `temperature`, so any current Claude model works out of the box.

**Choosing automatically based on which secrets exist** — rather than hardcoding a provider, a calling workflow can pass all three credentials through and compute `provider` from whichever secrets are actually set:

```yaml
      - name: Run Review Action
        uses: menil/pr-code-review-action@main
        with:
          provider: ${{ (secrets.ANTHROPIC_API_KEY != '' || secrets.CLAUDE_CODE_OAUTH_TOKEN != '') && 'anthropic' || 'openrouter' }}
          openrouter_api_key: ${{ secrets.OPENROUTER_API_KEY }}
          anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
          claude_code_oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
```
With this pattern: only `CLAUDE_CODE_OAUTH_TOKEN` set → uses your subscription; only `ANTHROPIC_API_KEY` set → uses billed API access; both set → the subscription token wins; neither set → falls back to OpenRouter.

### Private Repositories (Personal / Standalone Accounts)
If you fork the action into a private repository (which cannot be natively referenced by public workflows), check it out dynamically using a Personal Access Token (PAT) with `repo` scope first:

```yaml
name: Automated Code Review
on:
  pull_request:
    types: [opened, ready_for_review]

permissions:
  pull-requests: write
  contents: read

jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout Target Code
        uses: actions/checkout@v4

      - name: Checkout Private Review Action
        uses: actions/checkout@v4
        with:
          repository: your-username/pr-code-review-action
          token: ${{ secrets.PAT_WITH_REPO_ACCESS }}
          path: .github/actions/pr-code-review-action

      - name: Run PR Review Action
        uses: ./.github/actions/pr-code-review-action
        with:
          openrouter_api_key: ${{ secrets.OPENROUTER_API_KEY }}
```

## Configuration Inputs

| Input | Description | Required | Default |
|-------|-------------|----------|---------|
| `provider` | LLM provider: `openrouter` or `anthropic` | No | `openrouter` |
| `openrouter_api_key` | API key from OpenRouter | When `provider` is `openrouter` | - |
| `openrouter_model` | Model used for review | No | `openrouter/free` |
| `openrouter_base_url` | Base completions URL | No | `https://openrouter.ai/api/v1/chat/completions` |
| `openrouter_max_tokens`| Max completion tokens | No | `8192` |
| `anthropic_api_key` | API key from Anthropic (`sk-ant-...`); alternative to `claude_code_oauth_token` | When `provider` is `anthropic` (unless `claude_code_oauth_token` is set) | - |
| `claude_code_oauth_token` | Claude Pro/Max subscription OAuth token from `claude setup-token`; alternative to `anthropic_api_key` | When `provider` is `anthropic` (unless `anthropic_api_key` is set) | - |
| `anthropic_model` | Claude model used for review | No | `claude-haiku-4-5` |
| `anthropic_base_url` | Base Messages API URL | No | `https://api.anthropic.com/v1/messages` |
| `anthropic_max_tokens` | Max tokens for the Claude response | No | `8192` |
| `github_token` | GitHub token for posting reviews | No | `${{ github.token }}` |
| `post_summary` | Whether to post the high-level summary (PR-level overview of the review findings) as the review body | No | `true` |
| `exclude_patterns` | Custom regex patterns to exclude from review, separated by commas and/or newlines | No | `''` |

## Development

```bash
# Enter the nix development shell
nix-shell

# Run linting, formatting check, and test suite
just validate
```

If you use [direnv](https://direnv.net/), the checked-in `.envrc` loads the
nix shell automatically when you enter the directory:

```bash
direnv allow
just validate
```
