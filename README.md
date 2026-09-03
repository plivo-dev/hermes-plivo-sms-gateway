# hermes-plivo-sms

Plivo SMS channel for [Hermes Agent](https://github.com/NousResearch/hermes-agent). A text sent
to a Plivo number reaches the agent as a conversation turn, and the reply arrives back over
[Plivo](https://cx.plivo.com/?utm_source=github&utm_medium=oss&utm_campaign=hermes-plivo-sms-gateway)
as an SMS.

| Capability | Detail |
|---|---|
| Two-way SMS | Any Hermes agent becomes reachable by text |
| Sessions | Each sender number holds its own independent conversation |
| Formatting | Markdown becomes plain text, and a long reply splits across segments |
| Scheduled delivery | Cron and notification messages reach a home number without a live session |
| Provisioning | Plivo is configured automatically on every start, including a public URL |

Hermes provides the model turn. The plugin handles messaging transport, webhook security and the
Plivo-side configuration.

## Requirements

| | |
|---|---|
| Hermes Agent | With the gateway. Python 3.10 or later |
| Plivo | An account and an SMS-capable number |

`cloudflared` is fetched automatically when no public URL is configured.

## Installation

### 1. Install

```bash
pip install hermes-plivo-sms
```

### 2. Enable

```bash
hermes plugins enable plivo_sms-platform
hermes plugins list
```

The name includes the `-platform` suffix. Hermes strips it to derive the channel name
`plivo_sms`, and rejects the short form.

### 3. Add credentials

In `~/.hermes/.env`:

```dotenv
PLIVO_AUTH_ID=MAxxxxxxxxxxxxxxxxxx
PLIVO_AUTH_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
PLIVO_PHONE_NUMBER=+14155550100
```

Additional optional settings live in the same file and are listed under
[Configuration](#configuration). A pip-installed plugin does not appear in the `hermes config`
screens.

### 4. Start the gateway

```bash
hermes gateway run          # foreground
hermes gateway install      # or as a supervised service
```

Texting the number reaches the agent once the gateway is up. A temporary tunnel can take up to
2 minutes. Setting `PLIVO_SMS_PUBLIC_URL` removes the delay.

## Configuration

Read from `~/.hermes/.env`.

**Required**

| Variable | Purpose |
|---|---|
| `PLIVO_AUTH_ID` | Plivo auth ID |
| `PLIVO_AUTH_TOKEN` | Plivo auth token. Also verifies inbound webhook signatures |
| `PLIVO_PHONE_NUMBER` | The SMS-capable number the agent texts from, E.164 |

**Optional**

| Variable | Default | Purpose |
|---|---|---|
| `PLIVO_SMS_PUBLIC_URL` | unset | Public HTTPS base Plivo reaches the webhook on. Required for deployment. A temporary tunnel is opened when unset, and its URL rotates per run |
| `PLIVO_SMS_ALLOWED_USERS` | empty | Comma-separated sender numbers, digits only |
| `PLIVO_SMS_ALLOW_ALL_USERS` | `false` | `true` admits every sender |
| `PLIVO_SMS_HOME_CHANNEL` | unset | Number receiving cron and notification messages |
| `PLIVO_SMS_AUTO_WIRE` | `true` | `false` leaves the Plivo application and number untouched, to be configured by hand |
| `PLIVO_SMS_WEBHOOK_PORT` | `8090` | Local port for the webhook server |
| `PLIVO_SMS_WEBHOOK_HOST` | `127.0.0.1` | Local bind address for the webhook server |
| `PLIVO_SMS_INSECURE_NO_SIGNATURE` | `false` | `true` accepts unverified webhooks. Development only |

## Security

- Inbound webhooks are verified against Plivo's MA-V3 signature, using the account auth token
- Plivo applications and numbers the plugin did not create are never modified
- `PLIVO_SMS_ALLOWED_USERS` filters senders. Caller ID is spoofable, making it a filter rather
  than authentication
- `PLIVO_SMS_INSECURE_NO_SIGNATURE` weakens the above and belongs in development only

## Troubleshooting

| Symptom | Cause |
|---|---|
| `Plugin 'plivo_sms' is not installed or bundled` | The enable name needs the `-platform` suffix |
| No Plivo settings in `hermes config` | Expected for a pip-installed plugin. Edit `~/.hermes/.env` |
| Texts arrive, the agent never replies | A sender number in `PLIVO_SMS_ALLOWED_USERS` written with a leading `+` |
| Webhook rejects every message | The auth token in `~/.hermes/.env` differs from the account signing the webhook |
| Replies arrive truncated | A long reply splits across segments, and carriers may reorder them |

## Tests

```bash
python -m unittest discover -s tests
ruff check .
```

The suite covers the signature vectors, payload parsing, request validation and the pure
configuration helpers. It needs no Hermes install and touches no network.

## Limitations

- One Plivo number answers through one application, leaving the SMS and voice plugins unable to
  share a number
- Uninstalling leaves the Plivo application and number wired to a gateway no longer running

## License

MIT
