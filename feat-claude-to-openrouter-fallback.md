# AI Decision Record: Claude-to-OpenRouter Fallback on Token and Server Errors

## Context & Goal
The PR code review action supported two providers (`anthropic` and `openrouter`), but lacked dynamic runtime fallback. In scenarios where a user's Claude Pro/Max subscription expired (`CLAUDE_CODE_OAUTH_TOKEN`), credentials became invalid (`401/403`), or Anthropic experienced outages (`500/529`), workflows would fail immediately even when a valid `OPENROUTER_API_KEY` was configured in the environment.
The goal is to implement an automated fallback mechanism that prefers Claude Code subscription tokens, cascades to billed Anthropic API keys if set, and falls back to OpenRouter when Claude is unavailable or fails due to token errors or 5xx server errors, while immediately bubbling client errors (e.g. 400 Bad Request).

## Architecture & Key Decisions
1. **Default `provider: auto` Mode**:
   - `provider` defaults to `'auto'` across `action.yml` and Python entrypoint.
   - In `auto` mode, candidate providers are assembled in order of preference:
     1. Claude Pro/Max subscription OAuth token (`CLAUDE_CODE_OAUTH_TOKEN`)
     2. Anthropic billed API key (`ANTHROPIC_API_KEY`, if set)
     3. OpenRouter API key (`OPENROUTER_API_KEY`, if set)
   - When explicit `provider: anthropic` is requested, it strictly cascades between Anthropic credentials without falling back to OpenRouter, maintaining authorization boundaries and compliance isolation.
   - When explicit `provider: openrouter` is requested, it only invokes OpenRouter.
2. **Error Classification (`is_token_or_server_error`)**:
   - Accurately classifies token errors (HTTP 401 Unauthorized, 402 Payment Required, 403 Forbidden, 429 Rate Limit/Quota) and server errors (HTTP 500-599 including 529 Overloaded, `URLError`, `TimeoutError`).
   - Specifically handles Anthropic's credit exhaustion error (`"Your credit balance is too low..."`), which is returned under HTTP 400 with `invalid_request_error`.
   - Protects against client-side false positives: standard HTTP 400 Bad Request, 404, 413, 422, parameter errors, and model refusals fail immediately so configuration bugs are not masked.
3. **Fail-Fast Latency Optimization**:
   - When a token or server error occurs and a fallback candidate exists, `main()` breaks out of the retry loop immediately without executing `time.sleep()`, eliminating up to 6 seconds of dead time in CI.

## Alternatives Considered & Rejected
- **Workflow-level GitHub Actions Expression**:
  - *Rejected*: Using `${{ (secrets.ANTHROPIC_API_KEY != '' || secrets.CLAUDE_CODE_OAUTH_TOKEN != '') && 'anthropic' || 'openrouter' }}` only performs static credential presence checks; it cannot handle runtime token expiration, rate limits, or Anthropic 5xx outages.
- **Blind Fallback on Any Exception**:
  - *Rejected*: Falling back on arbitrary exceptions would catch HTTP 400 Bad Request (such as malformed prompt formats or invalid diff parameters) and silently retry against OpenRouter, masking prompt/parameter defects and potentially introducing prompt injection loops.
- **Falling Back to OpenRouter under `provider: anthropic`**:
  - *Rejected*: Explicit `provider: anthropic` should never send diffs to OpenRouter. Doing so violates tenant isolation and compliance requirements for users who strictly prohibit third-party aggregators.
