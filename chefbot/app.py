import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from logging.config import dictConfig
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, request
from google import genai
from scipy.spatial import distance
from slack_bolt import App
from slack_bolt.adapter.flask import SlackRequestHandler
from slugify import slugify
from zappa.asynchronous import task


load_dotenv('.env.private')

dictConfig({
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'standard': {
            'format': '{asctime} {levelname} {process} [{filename}:{lineno}] - {message}',
            'style': '{',
        }
    },
    'handlers': {
        'console': {
            'level': 'INFO',
            'class': 'logging.StreamHandler',
            'formatter': 'standard',
        },
    },
    'loggers': {
        '': {
            'handlers': ['console'],
            'level': 'DEBUG',
            'propagate': True,
        },
    },
})

logger = logging.getLogger(__name__)

PROMPT_TEMPLATE = """You are chefbot, a culinary assistant. Use a serious, professional tone and be concise.

It is currently {date} and your users are in Massachusetts unless they tell you otherwise. Keep this information in mind when responding. Try to use it to make seasonally appropriate suggestions, but be subtle about it (i.e., don't announce that you're doing this). For example, you should slightly prefer recipes for soups and stews in the winter and recipes using fresh vegetables in the spring and summer. You should also slightly prefer vegetarian options.

Call the `search_recipes` function to look up existing recipe information that may be relevant to the conversation. If a user mentions a recipe, you should try to look it up this way for more information. You can call `search_recipes` repeatedly with different queries. If an existing recipe is an appropriate response to a user message, return a Markdown link to the recipe - treating the recipe's filename as the URL - instead of reproducing the text of the recipe. If you can't find an existing recipe that fulfills the user's request, offer to create a new one that does. Only generate new information if you can't find existing recipes that are a good fit or if you're instructed to do so. When generating a new recipe, always ensure that you've called the `search_recipes` function at least once - query for "caldo verde" if you haven't already looked up some existing recipes - and use the same Markdown format used by the returned recipes for your new recipe, excluding the YAML frontmatter.

Never provide a list of equipment. Always provide ingredient amounts. Never provide a shopping list unless you're asked to do so, in which case you should exclude commonly stocked ingredients (e.g., salt, pepper, flour, sugar, olive oil, vegetable oil, sesame oil, etc.).
"""  # noqa

FRONTMATTER_TEMPLATE = """---
filename: {filename}
---
"""

EMBEDDING_MODEL = 'gemini-embedding-exp-03-07'
CHAT_MODEL = 'gemini-2.5-pro-preview-03-25'

# https://ai.google.dev/gemini-api/docs/pricing
MODELS = {
    'gemini-2.5-pro-preview-03-25': {
        'input_token_cost': 1.25 / 1000000,
        'output_token_cost': 10 / 1000000,
    },
    'gemini-2.0-flash-001': {
        'input_token_cost': 0.1 / 1000000,
        'output_token_cost': 0.4 / 1000000,
    },
    'gemini-embedding-exp-03-07': {
        'input_token_cost': 0,
    },
}

assert EMBEDDING_MODEL in MODELS, f'unknown EMBEDDING_MODEL {EMBEDDING_MODEL}, add it to MODELS'
assert CHAT_MODEL in MODELS, f'unknown CHAT_MODEL {CHAT_MODEL}, add it to MODELS'

CHEFBOT_USER_ID = 'U08E33CEFKK'
THINKING_SENTINEL = f'<@{CHEFBOT_USER_ID}> is thinking...'

IS_DEPLOYED = bool(os.environ.get('AWS_LAMBDA_FUNCTION_NAME'))
slack_app = App(
    token=os.environ.get('SLACK_BOT_TOKEN'),
    signing_secret=os.environ.get('SLACK_SIGNING_SECRET'),
    process_before_response=IS_DEPLOYED
)
user_name_cache = {}

flask_app = Flask(__name__)
handler = SlackRequestHandler(slack_app)

gemini = genai.Client(
    http_options=genai.types.HttpOptions(timeout=60 * 1000)
)


class Timer:
    def __init__(self):
        self.t0 = time.time()

    def done(self):
        self.latency = time.time() - self.t0


class ProgressMeter:
    def __init__(self, total, msg='{done}/{total} ({percent}%) done', mod=10):
        self.total = total
        self.done = 0
        self.msg = msg
        self.mod = mod

    def increment(self):
        self.done += 1

        if self.done % self.mod == 0:
            percent = round((self.done / self.total) * 100)
            print(self.msg.format(done=self.done, total=self.total, percent=percent))


def make_prompt():
    return PROMPT_TEMPLATE.format(date=datetime.now().strftime('%B %d'))


def estimate_cost(res):
    # embedding responses don't have usage_metadata
    if not hasattr(res, 'usage_metadata'):
        logger.info('no usage_metadata, unable to estimate cost')
        return 0

    input_cost = res.usage_metadata.prompt_token_count * MODELS[res.model_version]['input_token_cost']

    if res.usage_metadata.candidates_token_count:
        output_cost = res.usage_metadata.candidates_token_count * MODELS[res.model_version]['output_token_cost']
    else:
        # candidates_token_count is sometimes None, unclear why
        logger.info('no candidates_token_count, unable to estimate output cost')
        output_cost = 0

    return input_cost + output_cost


def embed_recipes():
    timer = Timer()

    recipes = {}
    for file in Path('../recipes').glob('*.md'):
        with file.open() as f:
            content = f.read().strip()
            if content:
                recipes[file.name] = content

    logger.info(f'embedding {len(recipes)} recipes using {EMBEDDING_MODEL}')

    progress = ProgressMeter(len(recipes))
    embeddings = {}
    cost = 0
    count = 0

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {}
        for filename, content in recipes.items():
            future = executor.submit(gemini.models.embed_content, model=EMBEDDING_MODEL, contents=content)
            futures[future] = filename
            progress.increment()

            # temporary, to stay under 10 RPM limit for gemini-embedding-exp-03-07
            count += 1
            if count == 10:
                logger.info('sleeping to respect rate limit')
                time.sleep(60)
                count = 0

        for future in as_completed(futures):
            # progress.increment()
            filename = futures[future]

            try:
                res = future.result()
            except:
                logger.exception(f'failed to embed {filename}')
                continue

            embeddings[filename] = {
                'content': recipes[filename],
                'embedding': res.embeddings[0].values,
            }
            cost += estimate_cost(res)

    with open('embeddings.json', 'w') as f:
        json.dump(embeddings, f, separators=(',', ':'))

    timer.done()
    logger.info(f'done in {round(timer.latency, 2)}s (cost: ${round(cost, 2)})')


def search_recipes(query: str) -> str:
    """Searches for existing recipes relevant to the provided query.

    Args:
        query: Text (e.g., word, phrase, sentence, etc.) describing recipe
          characteristics of interest (e.g., name, ingredients, instructions,
          cuisine, meal type, etc.).

    Returns:
        A string representation of relevant recipes, formatted as Markdown.
    """
    logger.info(f'search_recipes("{query}")')

    res = gemini.models.embed_content(model=EMBEDDING_MODEL, contents=query)
    query_embedding = res.embeddings[0].values

    with open('embeddings.json') as f:
        embeddings = json.load(f)

    recipes = []
    for filename, recipe in embeddings.items():
        recipes.append({
            'filename': filename,
            'distance': distance.cosine(query_embedding, recipe['embedding']),
        })

    recipes.sort(key=lambda recipe: recipe['distance'])

    docs = []
    for recipe in recipes[:25]:
        frontmatter = FRONTMATTER_TEMPLATE.format(filename=recipe['filename'])
        content = embeddings[recipe['filename']]['content']
        docs.append(f'{frontmatter}\n{content}')

    return '\n\n'.join(docs)


def get_user_name(user_id):
    if user_id not in user_name_cache:
        if user_id.startswith('B'):
            bot_info = slack_app.client.bots_info(bot=user_id)

            # Used to use these user names to populate the `name` field on OpenAI messages,
            # which required that they match the pattern ^[a-zA-Z0-9_-]+$. Not used after
            # switching to Gemini, but left as-is for now.
            user_name_cache[user_id] = slugify(bot_info['bot']['name'])
        else:
            user_info = slack_app.client.users_info(user=user_id)

            display_name = user_info['user']['profile']['display_name']
            real_name = user_info['user']['profile']['real_name']
            user_name_cache[user_id] = slugify(display_name or real_name)

    return user_name_cache[user_id]


def replace_user_mentions(text):
    pattern = r'<@([A-Z0-9]+)>'

    def replacer(match):
        user_id = match.group(1)
        user_name = get_user_name(user_id)

        return f'@{user_name}'

    return re.sub(pattern, replacer, text)


def replace_filenames(text):
    pattern = r'([a-zA-Z0-9_-]*\.md)'
    replacer = r'https://github.com/rlucioni/recipes/blob/master/recipes/\1'

    return re.sub(pattern, replacer, text)


def clean_code_blocks(text):
    pattern = r'```.*?\n'
    replacer = r'```\n'

    return re.sub(pattern, replacer, text)


# https://api.slack.com/reference/surfaces/formatting#basic-formatting
def to_mrkdwn(text):
    # markdown link like [link text](https://example.com)
    pattern = r'\[([^\]]+)\]\(([^)]+)\)'
    # slack mrkdwn link like <https://example.com|link text>
    replacer = r'<\2|\1>'

    return re.sub(pattern, replacer, text)


@task
def think(event):
    e2e_timer = Timer()
    logger.info(f'handling app mention using {CHAT_MODEL}')

    contents = []
    channel_id = event['channel']

    # Messages in a thread will have a thread_ts identifying their parent message.
    # Parent messages (with 0 or more replies) don't have a thread_ts.
    thread_ts = event.get('thread_ts')
    parent_ts = thread_ts if thread_ts else event['ts']

    replies = slack_app.client.conversations_replies(channel=channel_id, ts=parent_ts, limit=1000)

    for reply in replies['messages']:
        if reply['text'] == THINKING_SENTINEL:
            continue

        user_id = reply.get('user')
        if user_id:
            role = 'user'
            user_name = get_user_name(user_id)

        bot_id = reply.get('bot_id')
        if bot_id:
            role = 'model'
            user_name = get_user_name(bot_id)

        text = replace_user_mentions(reply['text'])
        parts = [
            # genai.types.Part.from_text(text=f'{user_name}: {text}')
            genai.types.Part.from_text(text=text)
        ]

        contents.append(genai.types.Content(role=role, parts=parts))

    if not IS_DEPLOYED:
        dumped_contents = [c.to_json_dict() for c in contents]
        contents_str = json.dumps(dumped_contents, indent=2)
        logger.info(f'contents are:\n{contents_str}')

    generation_timer = Timer()
    res = gemini.models.generate_content(
        model=CHAT_MODEL,
        config=genai.types.GenerateContentConfig(
            system_instruction=make_prompt(),
            temperature=0.7,
            tools=[search_recipes],
        ),
        contents=contents
    )
    generation_timer.done()

    cost = estimate_cost(res)
    content_with_urls = replace_filenames(res.text)
    cleaned_content = clean_code_blocks(content_with_urls)
    content_as_mrkdwn = to_mrkdwn(cleaned_content)

    if not IS_DEPLOYED:
        logger.info(f'sending response:\n{content_as_mrkdwn}')

    slack_app.client.chat_postMessage(
        channel=channel_id,
        text=content_as_mrkdwn,
        thread_ts=event['ts'],
        unfurl_links=False,
        unfurl_media=False,
    )

    e2e_timer.done()
    stats = {
        'e2e_latency (s)': round(e2e_timer.latency, 2),
        'generation_latency (s)': round(generation_timer.latency, 2),
        'cost': round(cost, 4),
    }

    stats_str = json.dumps(stats, indent=2)
    logger.info(f'stats:\n{stats_str}')


@slack_app.event('app_mention')
def respond_to_mention(event):
    channel_id = event['channel']
    slack_app.client.chat_postMessage(
        channel=channel_id,
        text=THINKING_SENTINEL,
        thread_ts=event['ts']
    )

    think(event)


@flask_app.route('/slack/events', methods=['POST'])
def slack_events():
    return handler.handle(request)


@flask_app.route('/')
def health():
    return 'ok'


def exception_handler(exception, event, context):
    logger.error('unhandled exception:', exc_info=exception)

    # Tells Zappa not to re-raise the exception, which in turn prevents Lambda
    # from retrying invocation.
    # https://github.com/zappa/Zappa/blob/0.59.0/zappa/handler.py#L252-L255
    return True
