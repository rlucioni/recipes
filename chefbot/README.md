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
GH_TOKEN
```

`GH_TOKEN` lets chefbot open pull requests against this repo. Create a [fine-grained personal access token](https://github.com/settings/personal-access-tokens/new) with access to only `rlucioni/recipes` and the "Contents" and "Pull requests" repository permissions set to "Read and write". Without it, everything else works but chefbot can't propose changes.

Generate `embeddings.json` from the recipes in `../recipes`. Only recipes that are new or have changed since the last run are re-embedded, and recipes that have been archived or deleted are dropped:

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

### Deploying on merge

The `deploy` GitHub Actions workflow (`../.github/workflows/deploy.yml`) runs on every push to `master` that touches `recipes/`, `chefbot/`, or the workflow itself, and can also be run by hand from the Actions tab. It does two independent things:

- Regenerates `../catalog.tsv` with `../scripts/update-catalog.sh` and commits it if it changed.
- Re-embeds new and changed recipes (`embeddings.json` is carried between runs in the Actions cache) and redeploys the function, so merged recipes show up in chefbot's searches within a few minutes.

To set it up once, create a service account the workflow can impersonate through Workload Identity Federation:

```bash
$ make github-deployer
```

Then add these repository secrets in GitHub under Settings > Secrets and variables > Actions: `GCP_WORKLOAD_IDENTITY_PROVIDER` and `GCP_SERVICE_ACCOUNT` (printed by the command above), plus `GEMINI_API_KEY`, `SLACK_BOT_TOKEN`, `SLACK_SIGNING_SECRET`, and `GH_TOKEN` (the same values as in `.env`). If `master` is protected, allow GitHub Actions to push to it so the catalog commit succeeds.

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

Slack requires event deliveries to be acknowledged within 3 seconds, but generating a response can take much longer than that. When an event arrives at `/slack/events`, the function verifies the request signature, posts a "thinking" placeholder in the thread, enqueues a Cloud Tasks task targeting its own `/think` endpoint, and acks. Cloud Tasks then invokes `/think`, which reads the thread, calls Gemini, and posts the reply. When running locally, thinking happens in the background of the same process instead.

Gemini has five tools. The first four are backed by `embeddings.json`:

- `search_recipes(query)` – semantic search over recipe content via embeddings. Returns the 25 most similar recipes in full.
- `list_recipes(course, prep_time, leftoverability, exclude_specialty_ingredients)` – exhaustive filtering on the YAML frontmatter that every recipe carries (see `../AGENTS.md` for the schema). This is the in-memory equivalent of `../catalog.tsv`.
- `get_recipes(filenames)` – opens specific recipes in full (e.g., to check ingredients or method).
- `sample_recipes(weights)` – weighted sampling without replacement, a port of the sampler script in the `pick-dinner` skill. The model assigns weights; the tool does the randomness. Draws are hashed from the Slack thread's parent `ts`, so re-sampling with the same weights later in the same thread reproduces the same order, which is how "show me more" continues down one list even though every Slack reply is a fresh model call.
- `propose_recipe_changes(title, summary, files)` – adds or edits recipes by committing complete files to a GitHub pull request. The prompt only allows this when a user explicitly asks to change the collection. Each Slack thread gets its own branch (`chefbot/<thread ts>`) and pull request, so follow-ups in the thread revise the same pull request, and `get_recipes` returns the pull request's unmerged drafts instead of the deployed versions. Files are checked against the frontmatter schema before anything is committed, and the pull request description links back to the Slack thread. Merging the pull request triggers the deploy workflow, which updates the catalog and chefbot's embeddings.

The system prompt explains the frontmatter schema and reproduces the `pick-dinner` skill in `../.cursor/skills` step for step: ask about effort, leftovers, and ingredients to use up; filter mains with `list_recipes`; weight each one using the skill's multipliers (ingredient match, season, specialty-ingredient trip on a weeknight), opening recipes with `get_recipes` where needed; order them with `sample_recipes`; suggest three at a time; then optionally pick a complementary side the same way. Expect a dinner pick to take several tool rounds and 10-20 seconds per reply.
