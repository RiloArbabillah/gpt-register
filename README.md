# ChatGPT Registration — Protocol-Only Outlook Edition

This project is a browser-free registration workflow. It uses `curl_cffi` TLS impersonation, Python/QuickJS OpenAI Sentinel proof-of-work, and Outlook IMAP XOAUTH2 to retrieve email OTPs through the OpenAI authorization flow.

It includes a lightweight WebUI for importing email accounts, starting registrations, viewing live SSE logs, and copying resulting credentials. Email providers include an Outlook account pool, a compatible Cloudflare temporary-email Worker, and IMAP catch-all email.

No payment integration, daemon, Camoufox, or Playwright is included.

## Highlights

- Browser-free protocol workflow with Sentinel token support.
- WebUI for account pools, registration runs, logs, and credential exports.
- Outlook, Cloudflare temporary-email, and IMAP catch-all mail providers.
- SMS providers: SmsBower, HeroSMS, and SMS-Activate-compatible APIs.
- Concurrent workers (1–20) with round-robin proxy pools.
- Automatic database migrations; existing `webui.db` is preserved during upgrades.
- Optional OAuth token exchange for `refresh_token` retrieval.

## Quick Start: WebUI

```bash
git clone https://github.com/RiloArbabillah/gpt-register.git
cd gpt-register
pip install -r requirements.txt
python3 start_webui.py
```

Open `http://127.0.0.1:8765/`. To expose the service on your network:

```bash
python3 start_webui.py --host 0.0.0.0 --port 8765
```

### Running on a Linux Server

Install Node.js 18 or later before starting the application. Node is required for the reliable QuickJS Sentinel path and email OTP delivery.

```bash
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo bash -
sudo apt-get install -y nodejs screen
node --version

screen -S webui
python3 start_webui.py --host 0.0.0.0 --port 8765
# Detach: Ctrl+A, then D
```

Useful `screen` commands:

```bash
screen -r webui
screen -ls
```

### Safe Upgrade

```bash
cd gpt-register
cp webui/webui.db webui/webui.db.backup
git pull
python3 start_webui.py
```

`webui.db` is ignored by Git and is not removed or overwritten by `git pull`. Database migrations only add required schema changes.

## Proxy Behavior and Country Allowlist

When no manual proxy is entered and direct connection is not explicitly enabled, the application downloads free HTTP CONNECT and SOCKS5 proxies from ProxyScrape. Only proxies that can reach an HTTPS endpoint are used.

The downloader requests separate ProxyScrape HTTP and SOCKS5 feeds for the country allowlist below, then verifies each record's protocol and `ip_data.countryCode` before adding it to the pool. A proxy with a missing country code, a mismatched protocol, or a country not listed here is rejected. The fastest working HTTP CONNECT and SOCKS5 proxies are selected together in one rotating pool. If no eligible proxy is available, the registration stops; it never silently falls back to a direct connection.

Allowed ProxyScrape countries (exactly as configured):

Albania, Algeria, Afghanistan, Åland Islands, Andorra, Angola, Antigua and Barbuda, Argentina, Armenia, Aruba, Australia, Austria, Azerbaijan, Bahamas, Bahrain, Bangladesh, Barbados, Belgium, Belize, Bermuda, Benin, Bhutan, Bolivia, Bosnia and Herzegovina, Botswana, Brazil, Brunei, Bulgaria, Burkina Faso, Burundi, Cabo Verde, Cambodia, Cameroon, Canada, Cayman Islands, Central African Republic, Chad, Chile, Colombia, Comoros, Congo (Brazzaville), Congo (DRC), Costa Rica, Côte d'Ivoire, Croatia, Cyprus, Czechia, Denmark, Djibouti, Dominica, Dominican Republic, Ecuador, Egypt, El Salvador, Equatorial Guinea, Eritrea, Estonia, Eswatini, Ethiopia, Faroe Islands, Fiji, Finland, France, French Guiana, French Polynesia, French Southern Territories, Gabon, Gambia, Georgia, Germany, Ghana, Greece, Grenada, Greenland, Guatemala, Guadeloupe, Guinea, Guinea-Bissau, Guyana, Haiti, Holy See, Honduras, Hungary, Iceland, India, Indonesia, Iraq, Ireland, Israel, Italy, Jamaica, Japan, Jordan, Kazakhstan, Kenya, Kiribati, Kuwait, Kyrgyzstan, Laos, Latvia, Lebanon, Lesotho, Liberia, Libya, Liechtenstein, Lithuania, Luxembourg, Madagascar, Malawi, Malaysia, Maldives, Mali, Malta, Marshall Islands, Martinique, Mauritania, Mauritius, Mayotte, Mexico, Micronesia, Moldova, Monaco, Mongolia, Montenegro, Morocco, Mozambique, Myanmar, Namibia, Nauru, Nepal, Netherlands, New Caledonia, New Zealand, Nicaragua, Niger, Nigeria, North Macedonia, Norway, Oman, Pakistan, Palau, Palestine, Panama, Papua New Guinea, Paraguay, Peru, Philippines, Poland, Portugal, Qatar, Réunion, Romania, Rwanda, Saint Barthélemy, Saint Helena, Saint Kitts and Nevis, Saint Lucia, Saint Martin (French part), Saint Pierre and Miquelon, Saint Vincent and the Grenadines, Samoa, San Marino, São Tomé and Príncipe, Saudi Arabia, Senegal, Serbia, Seychelles, Sierra Leone, Singapore, Slovakia, Slovenia, Solomon Islands, Somalia, South Africa, South Korea, South Sudan, Spain, Sri Lanka, Suriname, Sweden, Switzerland, Sudan, Svalbard and Jan Mayen, Taiwan, Tajikistan, Tanzania, Thailand, Timor-Leste, Togo, Tonga, Trinidad and Tobago, Tunisia, Turkey, Turkmenistan, Tuvalu, Uganda, Ukraine (with certain exceptions), United Arab Emirates, United Kingdom, United States of America, Uruguay, Uzbekistan, Vanuatu, Vietnam, Wallis and Futuna, Yemen, Zambia, and Zimbabwe.

For a manual proxy, set `PROXY` or enter it in the WebUI. SOCKS5 URLs are normalized from `socks5://` to `socks5h://`, so DNS resolution occurs through the proxy.

## Command Line

Run one registration using an Outlook four-part credential string:

```bash
python3 register_outlook.py 'email----password----client_id----refresh_token'
```

Test OTP retrieval only:

```bash
python3 mail_outlook.py 'email----password----client_id----refresh_token'
```

The required Outlook format is:

```text
email----password----client_id----microsoft_refresh_token
```

The `client_id` requires the `https://outlook.office.com/IMAP.AccessAsUser.All offline_access` scope.

On success, the application writes `account_<email>.json`, with fields such as `email`, `password`, `session_token`, `access_token`, `device_id`, `csrf_token`, `id_token`, `refresh_token`, and `cookie_header`.

## Installation

```bash
pip install -r requirements.txt
```

Install Node.js 18 or later to enable the QuickJS Sentinel path. The Python-only Sentinel path may pass the initial `/sentinel/req` request but can fail the deeper server-side validation used before OTP delivery.

```bash
# Ubuntu/Debian
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo bash -
sudo apt-get install -y nodejs
node --version
```

## Environment Variables

| Variable | Default | Description |
|---|---:|---|
| `PROXY` | — | Outbound proxy URL, for example `socks5://user:pass@host:port`. |
| `OTP_TIMEOUT` | `60` | OTP wait time in seconds; effective minimum is 30. |
| `WEBUI_ALLOW_LOGIN` | `0` | Set to `1` to use OTP login when an email is already registered. |
| `SKIP_OAUTH_TOKEN_EXCHANGE` | `0` | Set to `1` to skip OAuth refresh-token exchange. |
| `OAUTH_CODEX_RT_EXCHANGE` | `1` | Set to `1` to attempt the Codex OAuth refresh-token exchange. |
| `OAUTH_REFRESH_ONLY` | `0` | Set to `1` to request only a refresh token and skip session work. |
| `OPENAI_SENTINEL_NODE_PATH` | `node` | Path to the Node.js executable. |
| `LOGIN_PASSWORD` | — | Password used for the existing-account login branch. |
| `AUTH_HTTP_TRACE` | `0` | Set to `1` to log request method, URL, status, and cookies. |
| `AUTH_TRACE_DUMP` | `0` | Set to `1` to write full HTTP traces to `outputs/auth_trace_*.jsonl`. |
| `OPENAI_PHONE_NUMBER` | — | Manual phone number(s), comma-separated; used when SMS integration is disabled. |
| `OPENAI_PHONE_OTP` | — | Static six-digit phone OTP for debugging. |
| `OPENAI_PHONE_OTP_CMD` | — | Command whose stdout contains a six-digit phone OTP. |
| `OPENAI_PHONE_OTP_TIMEOUT` | `180` | Maximum phone-SMS wait time in seconds. |

## SMS Integration

OpenAI may require the `add-phone` step before a refresh token is available. Enable an SMS provider in the WebUI to rent a number, wait for the message, and submit the OTP automatically.

| Provider | Website | Notes |
|---|---|---|
| SmsBower | `smsbower.page` | Number reuse, V2 API, and automatic resend. |
| HeroSMS | `hero-sms.com` | SMS-Activate-compatible provider with broad country coverage. |

Configuration fields:

| Field | Description |
|---|---|
| `sms_enabled` | Main switch; when disabled, the `OPENAI_PHONE_NUMBER` flow is used. |
| `sms_provider` | `herosms`, `smsbower`, or `sms_activate`. |
| `sms_country` | Provider country code or ID; default is `52` (Thailand). |
| `sms_service` | OpenAI service code: `dr`. |
| `sms_max_price` | Maximum price per number for HeroSMS and SmsBower. |
| `sms_reuse_phone` | Reuse a number when the provider supports it. |
| `sms_phone_success_max` | Maximum registrations per reused number. |
| `sms_auto_country` | Select a provider's best available permitted country automatically. |

## Project Layout

| File | Purpose |
|---|---|
| `register_outlook.py` | Command-line entry point. |
| `auth_flow.py` | Protocol flow: CSRF, authorization, Sentinel, signup, OTP, account creation, callback, and token exchange. |
| `mail_outlook.py` | Outlook IMAP XOAUTH2 OTP retrieval. |
| `mail_cf.py` | Cloudflare temporary-email provider. |
| `sentinel.py` | Python Sentinel proof-of-work implementation. |
| `sentinel_quickjs.py` | QuickJS Sentinel implementation using the OpenAI SDK script. |
| `sms_provider.py` | SMS provider integrations and phone callback handling. |
| `http_client.py` | `curl_cffi` HTTP client with Chrome TLS impersonation. |
| `proxy_proxyscrape.py` | ProxyScrape downloader, country allowlist validation, and HTTPS CONNECT probe pool. |
| `webui/app.py` | FastAPI application and SSE logging endpoints. |
| `webui/db.py` | SQLite account-pool and registration-result storage. |
| `webui/registrar.py` | Registration worker and log callbacks. |

## Network Notes

- The default TLS fingerprint is `chrome136`; `chrome124` and `chrome120` are fallback profiles.
- CSRF requests retry Cloudflare 403 responses up to three times with exponential backoff.
- Outlook polling checks Inbox, Junk, Junk Email, and Spam folders.
- The known `tm1.openai.com` shadow-OTP pattern is filtered because it can return a fixed invalid code.
