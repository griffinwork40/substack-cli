# Authentication Setup — Substack Session Cookies

## Why cookies, not an API key

Substack has no self-serve API key for content, publishing, or subscriber
automation. The only way to access these endpoints programmatically is to
use the same session cookie your browser sends when you're logged into
substack.com. Substack does publish a "Developer API" but it only exposes
public-profile metadata under a restrictive ToS — not useful for the
operations this CLI performs.

## What to extract

| Cookie name | Required? | Notes |
|---|---|---|
| `connect.sid` | **Yes** (primary) | The main Express.js session cookie. |
| `substack.sid` | Recommended | Legacy cookie — some endpoints may key off this name specifically. |
| `substack.lli` | Optional | Secondary JWT-flavored cookie. Send if present. |

## Steps (DevTools manual extraction)

1. Log into `substack.com` (or your target publication) in a real browser.
2. Open DevTools (Cmd+Option+I on Mac, F12 on Windows).
3. Go to **Application** (Chrome) or **Storage** (Firefox) → **Cookies** → `https://substack.com`.
4. Copy the values of `connect.sid`, `substack.sid`, and `substack.lli` (whichever are present).
5. Store them in the CLI:

```bash
substack config set-cookies "connect.sid=s%3Alongtokenhere...; substack.sid=anothertoken...; substack.lli=maybepresent..."
substack config set-publication charliepgarcia
```

Or via environment variables (takes priority over the config file):

```bash
export SUBSTACK_COOKIES_STRING="connect.sid=...; substack.sid=..."
export SUBSTACK_PUBLICATION_URL="charliepgarcia"
```

### Verify your setup

```bash
substack config test --pretty
```

If it returns `{"status": "ok", "user": "Your Name"}`, your cookies are
valid and publication is configured correctly.

## Expiry

Substack session cookies are reported to last approximately 90 days. When
commands start failing with 401 or 403, your cookies have likely expired
— re-extract them using the steps above.

**Important:** Substack uses **both** 401 and 403 for "not authenticated"
inconsistently. Do not assume a 403 means "this route doesn't exist" —
it may simply mean your cookie has expired. The CLI's error messages will
surface the remediation hint on both status codes.

## Security warning

Treat the cookie string like a password:

- **Never** commit it to git (add `.config/substack-cli/` to `.gitignore` if you store config files in your repo).
- **Never** paste it into a shared chat, ticket, or AI prompt.
- Prefer the environment variable over the config file on shared machines.
- If you suspect a cookie has been compromised, log into Substack in your browser and sign out all sessions — this invalidates the old cookies.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| 401 on all authenticated commands | Cookie expired | Re-extract cookies from browser DevTools |
| 403 on all authenticated commands | Cookie expired OR route requires browser context | First try re-extracting; if only specific endpoints 403 while others work, see the browser-vs-curl gap note in `substack-api.md` |
| 403 only on `recommendations add` | Known browser-vs-curl gap | This endpoint rejects non-browser HTTP clients even with valid cookies — a known Substack limitation, not a cookie problem |
| `AuthError: No cookies found` | Neither env var nor config file set | Run `substack config set-cookies "..."` or `export SUBSTACK_COOKIES_STRING="..."` |
| `AuthError: No publication URL` | Neither env var nor config file set | Run `substack config set-publication charliepgarcia` or `export SUBSTACK_PUBLICATION_URL="charliepgarcia"` |