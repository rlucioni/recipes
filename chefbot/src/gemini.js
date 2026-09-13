import 'dotenv/config';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { GoogleGenAI } from '@google/genai';

export const CHAT_MODEL = 'gemini-3.8-flash';
export const EMBEDDING_MODEL = 'gemini-embedding-2';

// https://ai.google.dev/gemini-api/docs/pricing
export const MODELS = {
  'gemini-3.8-flash': {
    // Promotional pricing through December 31, 2026. Doubles to $1.50 / $7.50
    // starting January 1, 2027.
    inputTokenCost: 0.75 / 1000000,
    outputTokenCost: 3.75 / 1000000,
  },
  'gemini-embedding-2': {
    inputTokenCost: 0.2 / 1000000,
  },
};

if (!(CHAT_MODEL in MODELS)) {
  throw new Error(`unknown CHAT_MODEL ${CHAT_MODEL}, add it to MODELS`);
}
if (!(EMBEDDING_MODEL in MODELS)) {
  throw new Error(
    `unknown EMBEDDING_MODEL ${EMBEDDING_MODEL}, add it to MODELS`,
  );
}

// chefbot/
export const PROJECT_ROOT = fileURLToPath(new URL('..', import.meta.url));
export const EMBEDDINGS_PATH = fileURLToPath(
  new URL('../embeddings.json', import.meta.url),
);

export const gemini = new GoogleGenAI({
  apiKey: process.env.GEMINI_API_KEY,
  httpOptions: { timeout: 60 * 1000 },
});

export function estimateCost(res) {
  const usage = res.usageMetadata;

  // embedding responses don't have usageMetadata
  if (!usage) {
    console.info('no usageMetadata, unable to estimate cost');
    return 0;
  }

  const pricing = MODELS[res.modelVersion];
  if (!pricing) {
    console.info(
      `unknown model version ${res.modelVersion}, unable to estimate cost`,
    );
    return 0;
  }

  const inputCost = (usage.promptTokenCount ?? 0) * pricing.inputTokenCost;

  // thinking tokens are billed as output tokens
  const outputTokens =
    (usage.candidatesTokenCount ?? 0) + (usage.thoughtsTokenCount ?? 0);
  const outputCost = outputTokens * (pricing.outputTokenCost ?? 0);

  return inputCost + outputCost;
}

async function embed(text) {
  const res = await gemini.models.embedContent({
    model: EMBEDDING_MODEL,
    contents: text,
  });

  return res.embeddings[0].values;
}

// gemini-embedding-2 doesn't support taskType. Instead, retrieval tasks are
// expressed by prefixing queries and documents with instructions, which must be
// applied consistently at embedding time and at search time.
// https://ai.google.dev/gemini-api/docs/embeddings#task-types

export async function embedQuery(query) {
  return embed(`task: search result | query: ${query}`);
}

export async function embedDocument(content, title = 'none') {
  return embed(`title: ${title} | text: ${content}`);
}

export async function readEmbeddings() {
  return JSON.parse(await readFile(EMBEDDINGS_PATH, 'utf8'));
}
