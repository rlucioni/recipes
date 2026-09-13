# chefbot

A Slack bot that answers culinary questions using Gemini, grounded in the recipes in this repo.

## Quickstart

Use [nvm](https://github.com/creationix/nvm) to install Node.js and npm:

```bash
$ nvm install
$ nvm use
```

Install dependencies:

```bash
$ npm install
```

Create a `.env` file with the following variables:

```
GEMINI_API_KEY
SLACK_BOT_TOKEN
SLACK_SIGNING_SECRET
```

Generate `embeddings.json` from the recipes in `../recipes`. Only recipes that are new or have changed since the last run are re-embedded:

```bash
$ make embeddings
```

## Testing

To test the function locally:

```bash
$ make serve
```

Then ping the server to check that it's up:

```bash
$ make ping
```

To receive Slack events locally, expose the server with ngrok and point your Slack app's Events API Request URL at `https://<ngrok host>/slack/events`:

```bash
$ make tunnel
```

Run the linter:

```bash
$ make lint
```

## Deployment

Enable the required services:

```bash
$ make enable
```

Create the Cloud Tasks queue used to hand off thinking from the Slack event handler (only needed once):

```bash
$ make queue
```

Deploy the function to GCP. Note that `embeddings.json` is uploaded with the function, so run `make embeddings` first:

```bash
$ make deploy
```

Print the function's URL and set your Slack app's Events API Request URL to `<function URL>/slack/events`:

```bash
$ make url
```

The Slack app needs to subscribe to the `app_mention` bot event, plus `message.channels` (and `message.groups` for private channels) so that chefbot can respond to un-tagged follow-ups in threads it's already part of. The corresponding `channels:history` / `groups:history` scopes are also required to read threads.

Tail recent logs:

```bash
$ make logs
```

## How it works

chefbot responds when it's mentioned (`@chefbot ...`) and to any subsequent reply in a thread it has already responded in, so follow-ups don't need to re-tag it.

Slack requires event deliveries to be acknowledged within 3 seconds, but generating a response can take much longer than that. When an event arrives at `/slack/events`, the function verifies the request signature, posts a "thinking" placeholder in the thread, enqueues a Cloud Tasks task targeting its own `/think` endpoint, and acks. Cloud Tasks then invokes `/think`, which reads the thread, calls Gemini (using `search_recipes` to look up relevant recipes via embeddings), and posts the reply. When running locally, thinking happens in the background of the same process instead.
