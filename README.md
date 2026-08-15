# AI PR Code Reviewer Action

An automated Pull Request code reviewer powered by OpenRouter language models (like Gemini, Claude, Llama). Submits both high-level summaries and line-by-line inline review comments directly to GitHub PRs.

## Features
- Dynamic model swapping (Gemini, Claude, Llama, Llama-3, etc.) via OpenRouter.
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
        uses: menil/pr-code-review-action@v1
        with:
          openrouter_api_key: ${{ secrets.OPENROUTER_API_KEY }}
```

> [!TIP]
> This action is also available on the [GitHub Marketplace](https://github.com/marketplace/actions/pr-code-review-action). Pin to a release tag (e.g. `@v1`) rather than `@main` for stability.

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
| `openrouter_api_key` | API key from OpenRouter | Yes | - |
| `openrouter_model` | Model used for review | No | `openrouter/free` |
| `openrouter_base_url` | Base completions URL | No | `https://openrouter.ai/api/v1/chat/completions` |
| `openrouter_max_tokens`| Max completion tokens | No | `4096` |
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
