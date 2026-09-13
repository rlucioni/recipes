import { createHash, createHmac, timingSafeEqual } from 'node:crypto';
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

## Tools

Call the \`search_recipes\` function to look up existing recipe information that may be relevant to the conversation. If a user mentions a recipe, look it up this way for more information. You can call \`search_recipes\` repeatedly with different queries. If an existing recipe is an appropriate response to a user message, return a Markdown link to the recipe - treating the recipe's filename as the URL - instead of reproducing the text of the recipe. If you can't find an existing recipe that fulfills the user's request, create a new one that does. You should only generate new information if you can't find existing recipes that are a good fit or if the user instructs you to do so. When generating a new recipe, always ensure that you've called the \`search_recipes\` function at least once - query for "caldo verde" if you haven't already looked up some existing recipes - and use the same Markdown format used by the returned recipes for your new recipe, excluding the YAML frontmatter.

\`search_recipes\` returns only the {{SEARCH_RESULT_COUNT}} most similar recipes. When a question is about the collection as a whole (e.g., "how many desserts do we have?", "list every quick side", "what mains keep well?"), or when you need to browse by metadata rather than by meaning, call \`list_recipes\` instead. It filters on the metadata below and returns every match. Use \`get_recipes\` to open specific recipes by filename when you need their ingredients or method. Use \`sample_recipes\` whenever you need a random order; never invent randomness yourself.

## Recipe metadata

Every recipe begins with YAML frontmatter. The \`filename\` is the recipe's identity and URL. The other fields mean:

- \`course\`: one of \`breakfast\`, \`main\`, \`side\`, \`snack\`, \`component\`, \`bread\`, \`dessert\`, \`drink\`. Mains are full meals or center-of-the-plate savory dishes, including soups and dinner salads. Sides include vegetable sides, slaws, savory starches, and dips or spreads. Components are things never eaten on their own (doughs, stocks, sauces, syrups, rubs). Snacks are nibble food only.
- \`prep_time\`: total practical effort, including separately prepared components and cleanup. \`short\` is about 30 minutes or less. \`medium\` is reasonable on a weeknight. \`long\` is not realistic on a weeknight (overnight proofing, many components, deep frying). "Weeknight" or "low effort" means \`short\` or \`medium\`.
- \`leftoverability\` (mains only): \`low\` means eat it the day it's made. \`medium\` means fine the next day but quality drops (soggy, loses crispness, spoils quickly), or hard to scale. \`high\` means it keeps for several days, may improve with age, and batches easily. A \`_with_prep\` suffix means the cooked components keep well but the dish needs quick day-of assembly; treat it as its base level.
- \`specialty_ingredients\`: which special shopping trips the recipe needs beyond a pantry and a well-stocked American grocery store. \`seafood\` is fresh seafood other than shrimp or salmon. \`meat\` is unusual cuts or products such as duck, trotters, or specially cut ribs. \`other\` covers things like curry paste, tamarind, shrimp paste, and most Asian noodles. An empty list means no special trip. Fresh produce is always considered locally available.

## Picking dinner

When a user asks what to make for dinner, wants a dinner picker, or wants a shortlist of meals given time, leftovers, leftover ingredients, or the season, follow this procedure exactly. Don't add recipes to the collection unless asked.

### Questions

Find out three things. If the user's message doesn't already answer them, ask all three in a single message and wait for the reply before suggesting anything:

1. Effort: short, medium, long, or any. Maps to \`prep_time\`. "Weeknight" and "low or medium effort" mean short or medium.
2. Leftovers: low (tonight only), medium (fine tomorrow), high (several days), or any. Maps to \`leftoverability\`; \`medium_with_prep\` counts as medium and \`high_with_prep\` as high. If they don't care, don't filter on leftoverability at all.
3. Ingredients to use up: free text, optional.

### Filter

Call \`list_recipes\` with \`course\` set to \`main\` and \`prep_time\` set to the effort they chose (omit it for any). Add \`leftoverability\` only if they expressed a preference. Consider only these mains; never mix sides, breakfast, snacks, or other courses into the pool.

### Weight

Give every recipe in the pool a weight. Start at 1 and multiply:

- leftover-ingredient strong match: x4
- leftover-ingredient partial match: x2
- in-season produce or weather-appropriate: x2
- clearly off-season produce-forward: x0.5
- \`specialty_ingredients\` is non-empty and effort is short or medium (including weeknight / low or medium): x0.25

Do not drop a recipe only because it is off-season or needs a specialty trip, unless the user asked to avoid a special trip. Judge season from today's date, Northern Hemisphere, US produce: spring (Mar-May) asparagus, peas, lamb, lighter braises; summer (Jun-Aug) tomato, corn, zucchini, eggplant, cold dishes; fall (Sep-Nov) squash, apple, mushroom, cabbage, chili; winter (Dec-Feb) stews, braises, citrus, roots, hearty soups. If the user gave ingredients to use up, call \`get_recipes\` on plausible candidates from the pool to check whether they actually use those ingredients; otherwise open a recipe only when you need its ingredients or method to judge season.

### Sample

Do not pick by preference or always take the highest weights. Pass every pool recipe and its weight to \`sample_recipes\` and walk its output order for the rest of the conversation. The sampler is deterministic within a thread, so if the user asks for more later, repeat the filter, weight, and sample steps with the same inputs to recover the same order, then continue from where you left off.

### Suggest

Walk the sampled list in batches of 3. For each recipe, give a link and one sentence covering why it fits (effort, leftovers, ingredient, and/or season). After each batch, ask whether one of these works or whether they'd like to see the next 3. Never repeat a recipe already suggested in this thread.

- If they pick a recipe, stop paging. Then ask if they want to pick a side, and follow the side procedure only if they say yes.
- If none of them work, show the next 3 from the sampled list.
- If the list runs out, say so. If fewer than 3 remain, show whatever is left.

### Pick a side

Only after a main is chosen and the user wants a side. Call \`list_recipes\` with \`course\` set to \`side\` and the same \`prep_time\` filter as the dinner. Do not filter sides on leftoverability. Then filter by season: keep year-round sides and drop sides built around clearly off-season produce. Skip sides that repeat the main (another potato dish with a potato-forward dinner, rice with fried rice, bread with a sandwich).

Weight the rest from 1 with the dinner multipliers except leftovers (so in-season x2, off-season x0.5, specialty trip on a short or medium effort night x0.25), and also:

- complements well (contrast: greens or slaw with a starch-heavy main; starch with soup, stew, or chili): x4
- plausible pairing: x1
- poor pairing: skip

Call \`get_recipes\` on the chosen main and candidate sides when needed to judge the pairing. Sample with \`sample_recipes\` and walk the result in batches of 3 exactly as for dinner. If they pick one, stop. Otherwise continue until the list runs out.

### Slim pickings

If fewer than 3 dinners survive the filter, or the user rejects the whole sampled list, say the pool is thin, list whatever remains unshown, and suggest 2-3 dinners not in this collection that fit the same answers. Do not add those ideas to the collection unless asked.

## Rules

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
const GET_RECIPES_LIMIT = 25;
// Matches the floor used by the pick-dinner skill's sampler
const MIN_SAMPLE_WEIGHT = 0.05;

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
let catalogCache;

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

  return PROMPT_TEMPLATE.replace('{{DATE}}', date).replace(
    '{{SEARCH_RESULT_COUNT}}',
    String(SEARCH_RESULT_COUNT),
  );
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

// Recipe catalog

const COURSES = [
  'breakfast',
  'main',
  'side',
  'snack',
  'component',
  'bread',
  'dessert',
  'drink',
];
const PREP_TIMES = ['short', 'medium', 'long'];
const LEFTOVERABILITIES = [
  'low',
  'medium',
  'medium_with_prep',
  'high',
  'high_with_prep',
];

// Parses the restricted YAML frontmatter used by recipe files (see AGENTS.md):
// scalar `key: value` lines plus `specialty_ingredients` as either `[]` or a
// block list. Returns null if the content has no frontmatter.
function parseFrontmatter(content) {
  if (!content.startsWith('---\n')) {
    return null;
  }

  const end = content.indexOf('\n---', 4);
  if (end === -1) {
    return null;
  }

  const fields = {};
  let listKey;

  for (const line of content.slice(4, end).split('\n')) {
    const listItem = line.match(/^\s+-\s+(.+?)\s*$/);
    if (listItem && listKey) {
      fields[listKey].push(listItem[1]);
      continue;
    }

    const pair = line.match(/^([a-z_]+):\s*(.*?)\s*$/);
    if (!pair) {
      continue;
    }

    const [, key, value] = pair;
    if (value === '' || value === '[]') {
      fields[key] = [];
      listKey = key;
    } else {
      fields[key] = value;
      listKey = undefined;
    }
  }

  return fields;
}

async function getCatalog() {
  if (!catalogCache) {
    const embeddings = await getEmbeddings();

    catalogCache = Object.entries(embeddings)
      .map(([filename, recipe]) => {
        const fields = parseFrontmatter(recipe.content) ?? {};

        return {
          filename,
          course: fields.course,
          prep_time: fields.prep_time,
          leftoverability: fields.leftoverability,
          specialty_ingredients: fields.specialty_ingredients ?? [],
        };
      })
      .sort((a, b) => a.filename.localeCompare(b.filename));
  }

  return catalogCache;
}

function formatCatalog(recipes) {
  const header = [
    'filename',
    'course',
    'prep_time',
    'leftoverability',
    'specialty_ingredients',
  ].join('\t');

  const rows = recipes.map((recipe) =>
    [
      recipe.filename,
      recipe.course ?? '',
      recipe.prep_time ?? '',
      recipe.leftoverability ?? '',
      recipe.specialty_ingredients.join('|'),
    ].join('\t'),
  );

  return [header, ...rows].join('\n');
}

async function listRecipes({
  course,
  prep_time: prepTimes,
  leftoverability: leftoverabilities,
  exclude_specialty_ingredients: excludeSpecialty = false,
} = {}) {
  console.info(
    `list_recipes(${JSON.stringify({ course, prepTimes, leftoverabilities, excludeSpecialty })})`,
  );

  // `_with_prep` variants count as their base level.
  const leftoverSet = new Set(
    (leftoverabilities ?? []).flatMap((value) => [value, `${value}_with_prep`]),
  );
  const prepSet = new Set(prepTimes ?? []);

  const recipes = (await getCatalog()).filter(
    (recipe) =>
      (!course || recipe.course === course) &&
      (prepSet.size === 0 || prepSet.has(recipe.prep_time)) &&
      (leftoverSet.size === 0 || leftoverSet.has(recipe.leftoverability)) &&
      (!excludeSpecialty || recipe.specialty_ingredients.length === 0),
  );

  return `${recipes.length} matching recipes\n\n${formatCatalog(recipes)}`;
}

async function getRecipes({ filenames = [] } = {}) {
  console.info(`get_recipes(${JSON.stringify(filenames)})`);

  const embeddings = await getEmbeddings();
  const docs = [];
  const missing = [];

  for (const filename of filenames.slice(0, GET_RECIPES_LIMIT)) {
    const recipe = embeddings[filename];
    if (recipe) {
      docs.push(withFilename(filename, recipe.content));
    } else {
      missing.push(filename);
    }
  }

  const notes = [];
  if (missing.length) {
    notes.push(`No such recipes: ${missing.join(', ')}`);
  }
  if (filenames.length > GET_RECIPES_LIMIT) {
    notes.push(
      `Only the first ${GET_RECIPES_LIMIT} of ${filenames.length} requested recipes were returned.`,
    );
  }

  return [...notes, ...docs].join('\n\n');
}

// Weighted sampling

// Uniform in [0, 1), deterministic for a given (seed, filename). Using a hash
// instead of a stateful PRNG means each recipe's draw doesn't depend on the
// order or number of recipes being sampled, so repeating the sampling within
// a thread with the same weights yields the same order.
function hashToUnit(seed, filename) {
  const digest = createHash('sha256').update(`${seed}\n${filename}`).digest();

  return (digest.readUInt32BE(0) + 0.5) / 2 ** 32;
}

// Port of the pick-dinner skill's sampler: weighted sampling without
// replacement (Efraimidis-Spirakis), where each item gets the key
// random ** (1 / weight) and items are returned in descending key order.
function sampleRecipes({ weights = [] } = {}, { seed = 'default' } = {}) {
  console.info(`sample_recipes(${weights.length} weights, seed ${seed})`);

  const seen = new Set();
  const keyed = [];

  for (const { filename, weight } of weights) {
    if (!filename || seen.has(filename)) {
      continue;
    }
    seen.add(filename);

    const w = Math.max(Number(weight) || 0, MIN_SAMPLE_WEIGHT);
    keyed.push({ filename, key: hashToUnit(seed, filename) ** (1 / w) });
  }

  keyed.sort((a, b) => b.key - a.key);

  return keyed.map((item) => item.filename).join('\n');
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

const listRecipesDeclaration = {
  name: 'list_recipes',
  description:
    'Lists every existing recipe matching the given metadata filters, as tab-separated rows of filename, course, prep_time, leftoverability, and specialty_ingredients. Filters are optional; omit them all to list the whole collection. Unlike search_recipes, this is exhaustive and returns metadata only, not recipe content.',
  parameters: {
    type: Type.OBJECT,
    properties: {
      course: {
        type: Type.STRING,
        enum: COURSES,
        description: 'Only include recipes with this course.',
      },
      prep_time: {
        type: Type.ARRAY,
        items: { type: Type.STRING, enum: PREP_TIMES },
        description: 'Only include recipes with one of these prep_time values.',
      },
      leftoverability: {
        type: Type.ARRAY,
        items: { type: Type.STRING, enum: LEFTOVERABILITIES },
        description:
          'Only include recipes with one of these leftoverability values. `medium` also matches `medium_with_prep`, and `high` also matches `high_with_prep`. Only mains have leftoverability.',
      },
      exclude_specialty_ingredients: {
        type: Type.BOOLEAN,
        description:
          'If true, only include recipes whose specialty_ingredients list is empty, i.e. recipes that need no special shopping trip.',
      },
    },
  },
};

const getRecipesDeclaration = {
  name: 'get_recipes',
  description: `Returns the full content of specific existing recipes by filename, e.g. to check their ingredients or method. Use filenames exactly as returned by list_recipes or search_recipes. At most ${GET_RECIPES_LIMIT} recipes per call.`,
  parameters: {
    type: Type.OBJECT,
    properties: {
      filenames: {
        type: Type.ARRAY,
        items: { type: Type.STRING },
        description: 'Recipe filenames, e.g. ["caldo-verde.md"].',
      },
    },
    required: ['filenames'],
  },
};

const sampleRecipesDeclaration = {
  name: 'sample_recipes',
  description:
    'Randomly orders recipes according to the weights you assign, using weighted sampling without replacement: a recipe with weight 4 is four times as likely as a recipe with weight 1 to come first, but every recipe appears exactly once. Returns filenames one per line in sampled order. Within a single conversation thread the sampling is deterministic, so calling this again with the same weights returns the same order. Use this instead of choosing an order yourself.',
  parameters: {
    type: Type.OBJECT,
    properties: {
      weights: {
        type: Type.ARRAY,
        description: 'One entry per candidate recipe.',
        items: {
          type: Type.OBJECT,
          properties: {
            filename: { type: Type.STRING },
            weight: {
              type: Type.NUMBER,
              description: `Positive weight. Values below ${MIN_SAMPLE_WEIGHT} are raised to ${MIN_SAMPLE_WEIGHT}.`,
            },
          },
          required: ['filename', 'weight'],
        },
      },
    },
    required: ['weights'],
  },
};

const functionDeclarations = [
  searchRecipesDeclaration,
  listRecipesDeclaration,
  getRecipesDeclaration,
  sampleRecipesDeclaration,
];

async function callFunction(call, context) {
  switch (call.name) {
    case 'search_recipes':
      return { result: await searchRecipes(call.args.query) };
    case 'list_recipes':
      return { result: await listRecipes(call.args) };
    case 'get_recipes':
      return { result: await getRecipes(call.args) };
    case 'sample_recipes':
      return { result: sampleRecipes(call.args, context) };
    default:
      console.error(`model requested unknown function ${call.name}`);
      return { error: `unknown function ${call.name}` };
  }
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

// Threads

// Messages in a thread will have a thread_ts identifying their parent message.
// Parent messages (with 0 or more replies) don't have a thread_ts.
function getParentTs(event) {
  return event.thread_ts || event.ts;
}

async function getThread(event) {
  const replies = await slack.conversations.replies({
    channel: event.channel,
    ts: getParentTs(event),
    limit: 1000,
  });

  return replies.messages;
}

// chefbot always responds to mentions. It also responds to un-tagged replies in
// threads it has already participated in, so follow-ups don't need to re-tag it.
async function shouldRespond(event) {
  if (event.type === 'app_mention') {
    return true;
  }

  if (event.type !== 'message') {
    return false;
  }

  // Only plain replies from humans. Skips edits, deletions, bot messages
  // (including chefbot's own), and top-level channel messages.
  if (event.subtype || event.bot_id || !event.thread_ts) {
    return false;
  }

  // Mentions also arrive as app_mention events, which are handled above.
  if (event.text?.includes(`<@${CHEFBOT_USER_ID}>`)) {
    return false;
  }

  const thread = await getThread(event);

  return thread.some((message) => message.user === CHEFBOT_USER_ID);
}

// Thinking

// Generates a response to the conversation in `contents`, executing any
// function calls the model asks for along the way. `context.seed` makes
// sample_recipes deterministic within a thread. Returns the final text and
// the estimated cost across all rounds. Exported for local testing.
export async function generate(contents, context = {}) {
  let cost = 0;
  let res;

  // The JS SDK doesn't execute function calls for us like the Python SDK does,
  // so loop until the model stops asking for function calls.
  for (let round = 0; round <= MAX_FUNCTION_CALL_ROUNDS; round++) {
    res = await gemini.models.generateContent({
      model: CHAT_MODEL,
      config: {
        systemInstruction: makePrompt(),
        tools: [{ functionDeclarations }],
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
          response: await callFunction(call, context),
        },
      });
    }

    contents.push({ role: 'user', parts });
  }

  const text = res?.text;
  if (!text) {
    throw new Error('Gemini returned no text');
  }

  return { text, cost };
}

async function think(event) {
  const e2eTimer = new Timer();
  console.info(`handling ${event.type} using ${CHAT_MODEL}`);

  const contents = [];
  const channelId = event.channel;

  const replies = await getThread(event);

  for (const reply of replies) {
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
  const { text, cost } = await generate(contents, { seed: getParentTs(event) });
  generationTimer.done();

  const contentWithUrls = replaceFilenames(text);
  const cleanedContent = cleanCodeBlocks(contentWithUrls);
  const contentAsMrkdwn = toMrkdwn(cleanedContent);

  if (!IS_DEPLOYED) {
    console.info(`sending response:\n${contentAsMrkdwn}`);
  }

  await slack.chat.postMessage({
    channel: channelId,
    text: contentAsMrkdwn,
    thread_ts: getParentTs(event),
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
        thread_ts: getParentTs(event),
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
  if (body.type !== 'event_callback' || !event) {
    return res.status(200).send();
  }

  if (!(await shouldRespond(event))) {
    return res.status(200).send();
  }

  await slack.chat.postMessage({
    channel: event.channel,
    text: THINKING_SENTINEL,
    thread_ts: getParentTs(event),
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
