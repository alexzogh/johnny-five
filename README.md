# Johnny Five

A self-hosted Amazon Alexa custom skill that sends requests to Hermes or an existing Codex conversation. Replies use spoken summaries, Alexa app cards, and scrollable text on compatible Echo displays. The bridge runs on your Linux host; AWS compute services are not required. Amazon's Alexa service and a publicly reachable HTTPS endpoint are required.

## How it works

Alexa sends signed requests to `/alexa`. The bridge verifies Amazon signatures, timestamps, skill ID, and an explicit Amazon user allowlist. It stores a durable job, acknowledges promptly, and processes it in a background worker. Ask for the status of your last request to hear/read the result; completion does not push an unsolicited announcement.

Ordinary requests go to Hermes. Explicit “ask Codex” requests queue into your configured Codex conversation without interrupting an active task. Responses have a short spoken summary and a longer written answer. Compatible displays receive an APL screen; the Alexa app receives a Simple card. Cards/screens have size limits, so very long answers are marked shortened. Full answers remain in the connected agent.

Examples:

- “Alexa, ask Johnny Five to please explain solar battery terminology.”
- “Alexa, ask Johnny Five to ask Hermes to help me plan a project.”
- “Alexa, ask Johnny Five to ask Codex to summarize our pending work.”
- “Alexa, ask Johnny Five for the status of my last request.”

## Examples with your connected skills

**House status dashboard**

“Alexa, ask Johnny Five to ask Hermes to give me a dashboard of the status of the house.”

Or use the fixed shortcut: “Alexa, ask Johnny Five for a house briefing.” With Hermes connected to your Home Assistant tools and a house snapshot, the result can show temperatures, maintenance flags, low batteries, network/power readings, and pending tasks. The response is a text dashboard on a supported Echo screen or an Alexa app card, rather than a new web dashboard. Unavailable or historical readings should be labeled; this request does not control equipment.

**A turkey recipe from your library**

“Alexa, ask Johnny Five to ask Hermes to use the recipes library to find a recipe with turkey.”

With a recipe-library skill and source documents installed in Hermes, ask for servings, measured ingredients, cooking temperature, steps, and the source. Hermes supplies a short spoken overview and the complete recipe for the display/card. This repository does not include your recipe collection.

**Software development progress in Codex**

“Alexa, ask Johnny Five to ask Codex for the status of the software development update.”

For a clearer answer, name the project: “Alexa, ask Johnny Five to ask Codex for the status of the dashboard project and its remaining tests.” The request goes to the configured existing Codex conversation, so choose one with the relevant project context and workspace. This request asks for status; it does not authorize a deployment.

**Find a good movie in Plex tonight**

“Alexa, ask Johnny Five to ask Hermes to use Plex to find a good movie to watch tonight.”

Add a preference such as “under two hours” or “a science fiction movie we haven't watched.” Hermes needs a configured Plex tool or media-concierge skill with library access. Recommendations should distinguish titles already playable in your library from anything that still needs downloading. This example requests recommendations, not an automatic download.

The optional direct media shortcut is “Alexa, ask Johnny Five for something to watch”; that route requires the separately installed media concierge described below.

For each example, wait for the queue acknowledgment, then say: **“Alexa, ask Johnny Five for the status of my last request.”** If it is still working, ask again later. Devices without screens still speak the summary; written detail is available in the Alexa app.

## Private configuration

Copy the example **outside the checkout** and fill it locally:

```sh
mkdir -p ~/.config/johnny-five
chmod 700 ~/.config/johnny-five
install -m 600 config.example.json ~/.config/johnny-five/config.json
```

The bridge reads that file by default. Set `JOHNNY_FIVE_CONFIG` to use another path. Environment variables override file settings. The loader rejects configuration files readable by other users. Never upload the completed configuration, `.env`, authentication caches, agent conversations, or job database.

| Setting | Meaning |
| --- | --- |
| `ALEXA_SKILL_ID` | Skill ID from your Alexa developer console. Required. |
| `ALEXA_USER_IDS` | Comma-separated Amazon user IDs allowed to invoke the skill. Required. |
| `CODEX_THREAD_ID` | Existing Codex conversation ID. Required by this version. |
| `CODEX_BIN` | Path to the authenticated Codex executable. |
| `CODEX_WORKSPACE` | Workspace used for Codex requests. |
| `HERMES_URL` | Private Hermes API base URL, separate from its dashboard. |
| `HERMES_API_KEY` | Hermes API bearer key. |
| `STATE_DIRECTORY` | Private durable queue directory, outside the checkout. |
| `MEDIA_CONCIERGE_PATH` | Optional path to an independently installed compatible media concierge module. |

No Amazon developer username or password belongs in this configuration. Sign into the developer console separately to register the skill. Use the skill-specific `context.System.user.userId` from a simulator request, not an access token.

## Install on Linux

Use Python 3.11 or later. From your checkout:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest -v
.venv/bin/python bridge.py
```

The listener binds to `127.0.0.1:9120`. For persistent operation, adapt the paths in `johnny-five.service`, install it under `~/.config/systemd/user/`, then:

```sh
systemctl --user daemon-reload
systemctl --user enable --now johnny-five.service
```

The service template assumes `~/workspaces/johnny-five`. On an always-on host, user lingering may be needed for startup without an interactive login. Keep a single worker per state directory.

Hermes must expose its authenticated API privately to this host and accept `/v1/chat/completions` with the `hermes-agent` model. Its configured tools and integrations provide its abilities; this repository does not install Home Assistant integrations or agent skills.

Codex must already be authenticated with a managed app-server and an existing conversation. This bridge uses `codex queue`, the app-server's private Unix control socket, WebSockets, and paginated turn history. Those interfaces are version-sensitive; check compatibility with your installation. Do not expose the app-server or start a competing daemon. Queue acceptance alone is not treated as completion.

## Alexa developer console

1. Create a Custom skill named **Johnny Five**, English (US), **Provision your own**, **Start from Scratch**.
2. Paste `interaction-model.json` into the Interaction Model JSON editor; save and build.
3. Enable development testing for your own Amazon account. Configure the skill and user IDs in the private file.
4. Enable **Alexa Presentation Language** under Interfaces, save, and rebuild for Echo screen responses.
5. Publish only this bridge through a trusted HTTPS reverse proxy or Tailscale Funnel. With Tailscale configured, first inspect existing Serve/Funnel settings, then use `tailscale funnel --bg --https=443 9120` if that port is free. Tailnet approval may be required. Use the hostname reported by Tailscale.
6. In Endpoint select HTTPS, set `https://YOUR-HOST/alexa`, and choose a trusted certificate authority. Test in the simulator, then on an Echo using the same Amazon account.

A public GitHub repository is separate from publishing an Alexa skill. Development testing is sufficient for your own household. Do not expose Hermes, its dashboard, Home Assistant, the Codex daemon, or workspace files as part of this endpoint.

## Optional household/media intents

The included interaction model also contains house briefing, backlog, and media intents carried over from the original bridge. House/backlog requests expect Hermes to have an appropriate Home Assistant tool and a `sensor.house_briefing` snapshot; they do not create those resources. Adapt the fixed prompts for your own installation.

Media intents require a separately installed compatible module exposing a `stdio` CLI and `voice_answer`/`cached_status` functions. Set `MEDIA_CONCIERGE_PATH`, or install it at `../media-concierge/concierge.py`. That service and its private Plex/Sonarr/Radarr/NZBGet configuration are not included. Remove these optional intents from your Alexa model if unused.

## Permissions and recovery

The allowlist identifies an Amazon account, not the individual speaker. The bridge inherits the connected agents' permissions; prompt instructions are not a technical read-only sandbox. Review consequential actions in the agent UI. Codex approval requests are never granted automatically by the bridge. Do not use physical equipment actions merely to test connectivity.

Each Amazon request ID is processed once. A job interrupted by a crash becomes `uncertain` and is never automatically replayed. Check the agent conversation before retrying. Transport logs omit prompts, headers, and raw exceptions; the state database still contains private prompts and answers. Back it up privately.

To roll back, stop/disable `johnny-five.service`, remove only its HTTPS proxy/Funnel listener, and disable development testing in Amazon. Restore captured proxy settings if necessary. Preserve the state database rather than replacing new history with an old copy.

## References

- [Amazon: host a custom skill as a web service](https://developer.amazon.com/en-US/docs/alexa/custom-skills/host-a-custom-skill-as-a-web-service.html)
- [Amazon: APL support](https://developer.amazon.com/en-US/docs/alexa/alexa-presentation-language/apl-support-for-your-skill.html)
- [Hermes API server](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server)
- [Codex app-server](https://learn.chatgpt.com/docs/app-server)
