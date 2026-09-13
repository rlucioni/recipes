import { readdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import {
  EMBEDDING_MODEL,
  EMBEDDINGS_PATH,
  PROJECT_ROOT,
  embedDocument,
  readEmbeddings,
} from './gemini.js';

const RECIPES_DIR = path.resolve(PROJECT_ROOT, '../recipes');

// Number of embedding requests in flight at once. gemini-embedding-2 allows
// 3,000 RPM / 1M TPM, so this is well within limits.
// https://ai.google.dev/gemini-api/docs/rate-limits
const CONCURRENCY = 16;
const PROGRESS_INTERVAL = 10;

async function readRecipes() {
  const recipes = {};

  const filenames = await readdir(RECIPES_DIR);
  for (const filename of filenames.sort()) {
    if (!filename.endsWith('.md')) {
      continue;
    }

    const content = (
      await readFile(path.join(RECIPES_DIR, filename), 'utf8')
    ).trim();
    if (content) {
      recipes[filename] = content;
    }
  }

  return recipes;
}

// Runs fn over items with at most CONCURRENCY calls in flight at once.
async function forEachConcurrently(items, fn) {
  let next = 0;

  const workers = Array.from(
    { length: Math.min(CONCURRENCY, items.length) },
    async () => {
      while (next < items.length) {
        const item = items[next++];
        await fn(item);
      }
    },
  );

  await Promise.all(workers);
}

export async function embedRecipes({ memoized = true } = {}) {
  const t0 = Date.now();

  const recipes = await readRecipes();
  const total = Object.keys(recipes).length;
  console.info(`embedding ${total} recipes using ${EMBEDDING_MODEL}`);

  let embeddings = {};
  if (memoized) {
    try {
      embeddings = await readEmbeddings();
    } catch (error) {
      if (error.code !== 'ENOENT') {
        throw error;
      }
    }
  }

  // Drop recipes that have been archived or deleted so they stop showing up
  // in search results.
  for (const filename of Object.keys(embeddings)) {
    if (!(filename in recipes)) {
      console.info(`removing ${filename}, no longer a live recipe`);
      delete embeddings[filename];
    }
  }

  const pending = [];
  for (const [filename, content] of Object.entries(recipes)) {
    const recipe = embeddings[filename];
    if (
      recipe &&
      recipe.model === EMBEDDING_MODEL &&
      recipe.content === content
    ) {
      console.info(
        `skipping ${filename}, found existing recipe content embedding`,
      );
      continue;
    }

    pending.push(filename);
  }

  let done = 0;
  let failed = 0;

  await forEachConcurrently(pending, async (filename) => {
    console.info(`embedding recipe content for ${filename}`);

    try {
      const embedding = await embedDocument(recipes[filename], filename);

      embeddings[filename] = {
        model: EMBEDDING_MODEL,
        content: recipes[filename],
        embedding,
      };
    } catch (error) {
      failed += 1;
      console.error(`failed to embed ${filename}:`, error.message);
    }

    done += 1;
    if (done % PROGRESS_INTERVAL === 0 || done === pending.length) {
      const percent = Math.round((done / pending.length) * 100);
      console.info(`${done}/${pending.length} (${percent}%) done`);
    }
  });

  await writeFile(EMBEDDINGS_PATH, JSON.stringify(embeddings));

  const latency = (Date.now() - t0) / 1000;
  console.info(
    `done in ${latency.toFixed(2)}s (${pending.length - failed} embedded, ${failed} failed)`,
  );
}

await embedRecipes();
