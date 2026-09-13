import { createHmac, timingSafeEqual } from 'node:crypto';
import functions from '@google-cloud/functions-framework';
import { CloudTasksClient } from '@google-cloud/tasks';
import { Type } from '@google/genai';
import { WebClient } from '@slack/web-api';
import {
  CHAT_MODEL,
  embedQuery,
  estimateCost,
  gemini,
  readEmbeddings,
} from './gemini.js';

const PROMPT_TEMPLATE = `You are chefbot, a culinary assistant. Use a serious, professional tone and be concise.

It is currently {{DATE}} and your users are in Massachusetts unless they tell you otherwise. Keep this information in mind when responding. Try to use it to make seasonally appropriate suggestions, but be subtle about it (i.e., don't announce that you're doing this). For example, you should slightly prefer recipes for soups and stews in the winter and recipes using fresh vegetables in the spring and summer. You should also slightly prefer vegetarian options.

Call the \`search_recipes\` function to look up existing recipe information that may be relevant to the conversation. If a user mentions a recipe, look it up this way for more information. You can call \`search_recipes\` repeatedly with different queries. If an existing recipe is an appropriate response to a user message, return a Markdown link to the recipe - treating the recipe's filename as the URL - instead of reproducing the text of the recipe. If you can't find an existing recipe that fulfills the user's request, create a new one that does. You should only generate new information if you can't find existing recipes that are a good fit or if the user instructs you to do so. When generating a new recipe, always ensure that you've called the \`search_recipes\` function at least once - query for "caldo verde" if you haven't already looked up some existing recipes - and use the same Markdown format used by the returned recipes for your new recipe, excluding the YAML frontmatter.

Never provide a list of equipment. Always provide ingredient amounts. Never provide a shopping list unless you're asked to do so, in which case you should exclude commonly stocked ingredients (e.g., salt, pepper, flour, sugar, olive oil, vegetable oil, sesame oil, etc.).
`;

const FRONTMATTER_TEMPLATE = `---
filename: {{FILENAME}}
---
`;

// Adds the recipe's filename to its YAML frontmatter so the model can link to
// it. Recipes already have frontmatter (course, prep_time, etc.), in which
// case the filename is merged into the existing block rather than creating a
// second one.
function withFilename(filename, content) {
  if (content.startsWith('---\n')) {
    return content.replace('---\n', `---\nfilename: ${filename}\n`);
  }

  const frontmatter = FRONTMATTER_TEMPLATE.replace('{{FILENAME}}', filename);

  return `${frontmatter}\n${content}`;
}

const CHEFBOT_USER_ID = 'U08E33CEFKK';
const THINKING_SENTINEL = `<@${CHEFBOT_USER_ID}> is thinking...`;

// Python SDK default for automatic function calling
const MAX_FUNCTION_CALL_ROUNDS = 10;
const SEARCH_RESULT_COUNT = 25;

// Cloud Run sets K_SERVICE
const IS_DEPLOYED = Boolean(process.env.K_SERVICE);
const TASKS_LOCATION = 'us-central1';
const TASKS_QUEUE = 'chefbot';

const SLACK_BOT_TOKEN = process.env.SLACK_BOT_TOKEN;
const SLACK_SIGNING_SECRET = process.env.SLACK_SIGNING_SECRET;

const slack = new WebClient(SLACK_BOT_TOKEN);
const userNameCache = new Map();
let tasks;
let embeddingsCache;

// Timing

class Timer {
  constructor() {
    this.t0 = Date.now();
  }

  done() {
    this.latency = (Date.now() - this.t0) / 1000;
  }
}

// Prompting

function makePrompt() {
  const date = new Date().toLocaleDateString('en-US', {
    month: 'long',
    day: '2-digit',
    timeZone: 'America/New_York',
  });

  return PROMPT_TEMPLATE.replace('{{DATE}}', date);
}

// Recipe search

function cosineDistance(v1, v2) {
  if (v1.length !== v2.length) {
    throw new Error('embedding vectors must have equal length');
  }

  let dot = 0;
  let norm1 = 0;
  let norm2 = 0;
  for (let i = 0; i < v1.length; i++) {
    dot += v1[i] * v2[i];
    norm1 += v1[i] * v1[i];
    norm2 += v2[i] * v2[i];
  }

  if (norm1 === 0 || norm2 === 0) {
    return 1.0;
  }

  const cosineSimilarity = dot / (Math.sqrt(norm1) * Math.sqrt(norm2));

  return 1 - cosineSimilarity;
}

async function getEmbeddings() {
  if (!embeddingsCache) {
    embeddingsCache = await readEmbeddings();
  }

  return embeddingsCache;
}

async function searchRecipes(query) {
  console.info(`search_recipes("${query}")`);

  const queryEmbedding = await embedQuery(query);
  const embeddings = await getEmbeddings();

  const recipes = Object.entries(embeddings).map(([filename, recipe]) => ({
    filename,
    distance: cosineDistance(queryEmbedding, recipe.embedding),
  }));

  recipes.sort((a, b) => a.distance - b.distance);

  const docs = recipes
    .slice(0, SEARCH_RESULT_COUNT)
    .map((recipe) =>
      withFilename(recipe.filename, embeddings[recipe.filename].content),
    );

  return docs.join('\n\n');
}

const searchRecipesDeclaration = {
  name: 'search_recipes',
  description: 'Searches for existing recipes relevant to the provided query.',
  parameters: {
    type: Type.OBJECT,
    properties: {
      query: {
        type: Type.STRING,
        description:
          'Text (e.g., word, phrase, sentence, etc.) describing recipe characteristics of interest (e.g., name, ingredients, instructions, cuisine, meal type, etc.).',
      },
    },
    required: ['query'],
  },
};

async function callFunction(call) {
  if (call.name === 'search_recipes') {
    return { result: await searchRecipes(call.args.query) };
  }

  console.error(`model requested unknown function ${call.name}`);
  return { error: `unknown function ${call.name}` };
}

// Slack text munging

function slugify(text) {
  return text
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

async function getUserName(userId) {
  if (!userNameCache.has(userId)) {
    if (userId.startsWith('B')) {
      const botInfo = await slack.bots.info({ bot: userId });
      userNameCache.set(userId, slugify(botInfo.bot.name));
    } else {
      const userInfo = await slack.users.info({ user: userId });

      const displayName = userInfo.user.profile.display_name;
      const realName = userInfo.user.profile.real_name;
      userNameCache.set(userId, slugify(displayName || realName));
    }
  }

  return userNameCache.get(userId);
}

async function replaceUserMentions(text) {
  const pattern = /<@([A-Z0-9]+)>/g;

  const userIds = [...new Set([...text.matchAll(pattern)].map((m) => m[1]))];
  const userNames = new Map();
  for (const userId of userIds) {
    userNames.set(userId, await getUserName(userId));
  }

  return text.replace(pattern, (_match, userId) => `@${userNames.get(userId)}`);
}

function replaceFilenames(text) {
  const pattern = /([a-zA-Z0-9_-]*\.md)/g;
  const replacer = 'https://github.com/rlucioni/recipes/blob/master/recipes/$1';

  return text.replace(pattern, replacer);
}

function cleanCodeBlocks(text) {
  const pattern = /```.*?\n/g;
  const replacer = '```\n';

  return text.replace(pattern, replacer);
}

// https://api.slack.com/reference/surfaces/formatting#basic-formatting
function toMrkdwn(text) {
  // markdown link like [link text](https://example.com)
  const pattern = /\[([^\]]+)\]\(([^)]+)\)/g;
  // slack mrkdwn link like <https://example.com|link text>
  const replacer = '<$2|$1>';

  return text.replace(pattern, replacer);
}

// Signatures

function sign(message) {
  return `v0=${createHmac('sha256', SLACK_SIGNING_SECRET).update(message).digest('hex')}`;
}

function signaturesMatch(expected, actual) {
  if (!actual) {
    return false;
  }

  const expectedBuffer = Buffer.from(expected);
  const actualBuffer = Buffer.from(actual);

  return (
    expectedBuffer.length === actualBuffer.length &&
    timingSafeEqual(expectedBuffer, actualBuffer)
  );
}

// https://api.slack.com/authentication/verifying-requests-from-slack
function verifySlackRequest(req) {
  const timestamp = req.get('x-slack-request-timestamp');
  const signature = req.get('x-slack-signature');

  if (!timestamp || !signature || !req.rawBody) {
    return false;
  }

  // reject stale requests to guard against replay attacks
  const age = Math.abs(Date.now() / 1000 - Number(timestamp));
  if (Number.isNaN(age) || age > 60 * 5) {
    return false;
  }

  const expected = sign(`v0:${timestamp}:${req.rawBody.toString('utf8')}`);

  return signaturesMatch(expected, signature);
}

function verifyTaskRequest(req) {
  if (!req.rawBody) {
    return false;
  }

  const expected = sign(req.rawBody.toString('utf8'));

  return signaturesMatch(expected, req.get('x-chefbot-signature'));
}

// Thinking

async function think(event) {
  const e2eTimer = new Timer();
  console.info(`handling app mention using ${CHAT_MODEL}`);

  const contents = [];
  const channelId = event.channel;

  // Messages in a thread will have a thread_ts identifying their parent message.
  // Parent messages (with 0 or more replies) don't have a thread_ts.
  const parentTs = event.thread_ts || event.ts;

  const replies = await slack.conversations.replies({
    channel: channelId,
    ts: parentTs,
    limit: 1000,
  });

  for (const reply of replies.messages) {
    if (reply.text === THINKING_SENTINEL) {
      continue;
    }

    const role = reply.bot_id ? 'model' : 'user';
    const text = await replaceUserMentions(reply.text);

    contents.push({ role, parts: [{ text }] });
  }

  if (!IS_DEPLOYED) {
    console.info(`contents are:\n${JSON.stringify(contents, null, 2)}`);
  }

  const generationTimer = new Timer();
  let cost = 0;
  let res;

  // The JS SDK doesn't execute function calls for us like the Python SDK does,
  // so loop until the model stops asking for function calls.
  for (let round = 0; round <= MAX_FUNCTION_CALL_ROUNDS; round++) {
    res = await gemini.models.generateContent({
      model: CHAT_MODEL,
      config: {
        systemInstruction: makePrompt(),
        tools: [{ functionDeclarations: [searchRecipesDeclaration] }],
      },
      contents,
    });
    cost += estimateCost(res);

    const calls = res.functionCalls;
    if (!calls?.length) {
      break;
    }

    // Append the model's turn verbatim so thought signatures are preserved.
    contents.push(res.candidates[0].content);

    const parts = [];
    for (const call of calls) {
      parts.push({
        functionResponse: {
          id: call.id,
          name: call.name,
          response: await callFunction(call),
        },
      });
    }

    contents.push({ role: 'user', parts });
  }
  generationTimer.done();

  const text = res?.text;
  if (!text) {
    throw new Error('Gemini returned no text');
  }

  const contentWithUrls = replaceFilenames(text);
  const cleanedContent = cleanCodeBlocks(contentWithUrls);
  const contentAsMrkdwn = toMrkdwn(cleanedContent);

  if (!IS_DEPLOYED) {
    console.info(`sending response:\n${contentAsMrkdwn}`);
  }

  await slack.chat.postMessage({
    channel: channelId,
    text: contentAsMrkdwn,
    thread_ts: event.ts,
    unfurl_links: false,
    unfurl_media: false,
  });

  e2eTimer.done();
  const stats = {
    'e2e_latency (s)': Number(e2eTimer.latency.toFixed(2)),
    'generation_latency (s)': Number(generationTimer.latency.toFixed(2)),
    cost: Number(cost.toFixed(4)),
  };

  console.info(`stats:\n${JSON.stringify(stats, null, 2)}`);
}

async function thinkSafely(event) {
  try {
    await think(event);
  } catch (error) {
    console.error('error while thinking:', error);

    try {
      await slack.chat.postMessage({
        channel: event.channel,
        text: `Sorry, something went wrong: ${error.message}`,
        thread_ts: event.ts,
      });
    } catch (slackError) {
      console.error('failed to post error to Slack:', slackError);
    }
  }
}

// Slack expects an ack within 3 seconds, and thinking takes much longer than
// that. When deployed, hand the event off to Cloud Tasks, which invokes this
// same function again at /think. Locally, just think in the background.
async function enqueueThink(req, event) {
  if (!IS_DEPLOYED) {
    thinkSafely(event);
    return;
  }

  tasks ??= new CloudTasksClient();

  const project = await tasks.getProjectId();
  const parent = tasks.queuePath(project, TASKS_LOCATION, TASKS_QUEUE);

  // Target whatever host and path prefix Slack is using to reach us.
  const basePath = req.path.replace(/\/slack\/events\/?$/, '');
  const url = `https://${req.get('host')}${basePath}/think`;

  const body = JSON.stringify({ event });

  await tasks.createTask({
    parent,
    task: {
      httpRequest: {
        httpMethod: 'POST',
        url,
        headers: {
          'Content-Type': 'application/json',
          'X-Chefbot-Signature': sign(body),
        },
        body: Buffer.from(body).toString('base64'),
      },
    },
  });

  console.info(`enqueued think task targeting ${url}`);
}

// HTTP handlers

async function handleSlackEvents(req, res) {
  if (!verifySlackRequest(req)) {
    console.error('invalid Slack signature');
    return res.status(401).send('invalid signature');
  }

  const body = req.body;

  // https://api.slack.com/events/url_verification
  if (body.type === 'url_verification') {
    return res.status(200).json({ challenge: body.challenge });
  }

  // Slack retries events that aren't acked within 3 seconds. Don't handle
  // the same event twice.
  if (req.get('x-slack-retry-num')) {
    console.info(
      `ignoring Slack retry (reason: ${req.get('x-slack-retry-reason')})`,
    );
    return res.status(200).set('x-slack-no-retry', '1').send();
  }

  const event = body.event;
  if (body.type !== 'event_callback' || event?.type !== 'app_mention') {
    return res.status(200).send();
  }

  await slack.chat.postMessage({
    channel: event.channel,
    text: THINKING_SENTINEL,
    thread_ts: event.ts,
  });

  await enqueueThink(req, event);

  return res.status(200).send();
}

async function handleThink(req, res) {
  if (!verifyTaskRequest(req)) {
    console.error('invalid task signature');
    return res.status(401).send('invalid signature');
  }

  await thinkSafely(req.body.event);

  // Always ack so Cloud Tasks doesn't retry.
  return res.status(200).send('ok');
}

functions.http('chefbot', async (req, res) => {
  try {
    if (req.method === 'POST' && /\/slack\/events\/?$/.test(req.path)) {
      return await handleSlackEvents(req, res);
    }

    if (req.method === 'POST' && /\/think\/?$/.test(req.path)) {
      return await handleThink(req, res);
    }

    if (req.method === 'GET') {
      return res.status(200).send('ok');
    }

    return res.status(404).send('not found');
  } catch (error) {
    console.error('unhandled error:', error);
    return res.status(500).send('internal server error');
  }
});
