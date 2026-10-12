const REPO = 'rlucioni/recipes';
const OWNER = REPO.split('/')[0];
const API_URL = `https://api.github.com/repos/${REPO}`;
export const BASE_BRANCH = 'master';

const GH_TOKEN = process.env.GH_TOKEN;
export const githubEnabled = Boolean(GH_TOKEN);

// https://docs.github.com/en/rest
// GETs return null for 404s so callers can check whether things exist.
async function request(method, path, body) {
  const res = await fetch(`${API_URL}${path}`, {
    method,
    headers: {
      Accept: 'application/vnd.github+json',
      Authorization: `Bearer ${GH_TOKEN}`,
      'X-GitHub-Api-Version': '2022-11-28',
    },
    body: body && JSON.stringify(body),
  });

  if (res.status === 404 && method === 'GET') {
    return null;
  }
  if (!res.ok) {
    throw new Error(
      `GitHub ${method} ${path} failed with ${res.status}: ${await res.text()}`,
    );
  }

  return res.json();
}

async function getFile(path, ref) {
  const file = await request('GET', `/contents/${path}?ref=${ref}`);

  return file && Buffer.from(file.content, 'base64').toString('utf8');
}

export async function findOpenPullRequest(branch) {
  const pulls = await request(
    'GET',
    `/pulls?state=open&head=${OWNER}:${encodeURIComponent(branch)}`,
  );

  return pulls?.[0] ?? null;
}

// Maps each recipe filename the pull request touches to its content at the
// pull request's head, or to null if the pull request removes it.
export async function getPullRequestRecipes(pr) {
  const files = await request('GET', `/pulls/${pr.number}/files?per_page=100`);
  const recipes = new Map();

  for (const file of files) {
    const match = file.filename.match(/^recipes\/([^/]+\.md)$/);
    if (!match) {
      continue;
    }

    recipes.set(
      match[1],
      file.status === 'removed'
        ? null
        : await getFile(file.filename, pr.head.sha),
    );
  }

  return recipes;
}

// Commits `files` (a map of repo path to content) to `branch` as a single
// commit. If `pr` is the branch's open pull request, the commit goes on top of
// it. Otherwise the branch is created from (or, if left over from a closed pull
// request, reset to) BASE_BRANCH and a new pull request is opened.
export async function proposeChanges({ branch, pr, files, title, body }) {
  const parentSha = pr
    ? pr.head.sha
    : (await request('GET', `/git/ref/heads/${BASE_BRANCH}`)).object.sha;
  const parent = await request('GET', `/git/commits/${parentSha}`);

  const tree = await request('POST', '/git/trees', {
    base_tree: parent.tree.sha,
    tree: Object.entries(files).map(([path, content]) => ({
      path,
      mode: '100644',
      type: 'blob',
      content,
    })),
  });

  if (tree.sha === parent.tree.sha) {
    return { pr, changed: false };
  }

  const commit = await request('POST', '/git/commits', {
    message: title,
    tree: tree.sha,
    parents: [parentSha],
  });

  if (pr) {
    await request('PATCH', `/git/refs/heads/${branch}`, { sha: commit.sha });
    return { pr, changed: true };
  }

  if (await request('GET', `/git/ref/heads/${branch}`)) {
    await request('PATCH', `/git/refs/heads/${branch}`, {
      sha: commit.sha,
      force: true,
    });
  } else {
    await request('POST', '/git/refs', {
      ref: `refs/heads/${branch}`,
      sha: commit.sha,
    });
  }

  const created = await request('POST', '/pulls', {
    title,
    head: branch,
    base: BASE_BRANCH,
    body,
  });

  return { pr: created, changed: true };
}
