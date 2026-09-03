# hermes-plivo-sms

A real-time two-way SMS channel for the [Hermes agent](https://github.com/NousResearch/hermes-agent) over [Plivo](https://cx.plivo.com/?utm_source=github&utm_medium=oss&utm_campaign=hermes-plivo-sms). A text sent to a Plivo phone number reaches the agent as a conversation turn, the agent reasons and replies, and the reply arrives back as an SMS. Each sender number holds its own independent conversation with the agent.

The plugin registers the `plivo_sms` platform. Hermes provides the AI. The plugin provides the messaging transport, the webhook security, and the Plivo-side configuration.

## Features

- Two-way SMS conversations with any Hermes agent, one session per sender number
- Inbound webhook authentication with Plivo's MA-V3 signature, verified against real captured traffic
- Autonomous configuration at startup. The plugin hosts its own webhook server, obtains a public URL, and wires the Plivo application and phone number over the REST API, idempotently on every start
- Markdown stripped to plain text before delivery, with long replies split across message segments
- Cron and notification delivery to a home number through a standalone sender that needs no live gateway session

## Requirements

- A Hermes agent runtime with the gateway
- Python 3.10 or newer
- A [Plivo account](https://cx.plivo.com/?utm_source=github&utm_medium=oss&utm_campaign=hermes-plivo-sms) with an SMS-capable phone number
- Optional, for the zero-setup development tunnel, the `cloudflared` binary

## Installation

```bash
git clone https://github.com/plivo/hermes-plivo-sms
cd hermes-plivo-sms
pip install .
```

The Hermes gateway discovers the plugin through the `hermes_agent.plugins` entry point and loads it at startup. Confirm discovery with:

```bash
hermes plugins list
```

Then enable the plugin. Hermes knows a pip-installed plugin by its entry-point name, and derives the platform name from it by stripping the suffix, so the plugin is `plivo_sms-platform` while the platform it registers is `plivo_sms`.

```bash
hermes plugins enable plivo_sms-platform
```

## Configuration

Add the following to `~/.hermes/.env`.

| Variable | Required | Description |
|---|---|---|
| `PLIVO_AUTH_ID` | yes | Plivo account auth ID |
| `PLIVO_AUTH_TOKEN` | yes | Plivo account auth token, also used to verify inbound webhook signatures |
| `PLIVO_PHONE_NUMBER` | yes | The SMS-capable number the agent texts from |
| `PLIVO_SMS_PUBLIC_URL` | no | A public https base URL for the webhook server, for example a server host or a named tunnel. When absent, the plugin launches a Cloudflare quick tunnel, which is suitable for development because its URL rotates per run |
| `PLIVO_SMS_AUTO_WIRE` | no | Set to `false` to skip the automatic Plivo application and number wiring (default `true`) |

Start the Hermes gateway and text the number. There are no further setup steps. The plugin creates or updates a Plivo application named `hermes-plivo-sms`, points its message URL at the webhook server, and attaches the phone number, checking before each write so repeated starts are safe.

Additional optional variables cover port and host selection (`PLIVO_SMS_WEBHOOK_PORT`, default 8090, and `PLIVO_SMS_WEBHOOK_HOST`), access control (`PLIVO_SMS_ALLOWED_USERS` and `PLIVO_SMS_ALLOW_ALL_USERS`), and scheduled delivery (`PLIVO_SMS_HOME_CHANNEL`).

## How it works

At startup the plugin brings up an aiohttp server with the message webhook, resolves its public URL, and configures the Plivo side in the background. When a text arrives, Plivo posts it to the webhook, the plugin verifies the MA-V3 signature against the account auth token, and the message is dispatched to the agent under a session keyed to the sender's number. The webhook acknowledges immediately while the agent works, and the reply goes out through the Plivo Messages API from the configured number.

Replies are converted from markdown to plain text, since SMS renders markup as literal characters, and messages longer than one segment budget are split and sent in order.

## Testing

```bash
python -m unittest discover -s tests
```

The suite covers the signature vectors, the payload parsing, the request validation, and the pure configuration helpers. It runs without a Hermes install and touches no network.

## Related

Outbound tools ship separately as `hermes-plivo-tools`, which gives the agent `send_sms`, `make_call`, and `lookup_number` without hosting a channel. A live voice channel ships as `hermes-plivo-voice`.

## License

MIT
