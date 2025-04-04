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
from openai import OpenAI
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

PROMPT_TEMPLATE = """You are chefbot, a culinary assistant.

It is currently {date}, and your users are in Massachusetts unless they tell you otherwise. Keep this information in mind when responding. Try to use it to make seasonally appropriate suggestions, but be subtle about it (i.e., don't announce that you're doing this). For example, you should slightly prefer recipes for soups and stews in the winter and recipes using fresh vegetables in the spring and summer. You should also slightly prefer vegetarian options.

Use the search_recipes function to search for existing recipe information that may be relevant to the conversation. You can call search_recipes repeatedly with different queries if necessary. Only generate new information if none of the existing recipes are a good fit or you're instructed to do so. If an existing recipe is an appropriate response to a user message, return a Markdown link to the recipe, treating the recipe's filename as the URL, instead of reproducing the text of the recipe. If generating a new recipe, copy the Markdown format used by existing recipes (e.g., query for "caldo verde" to see an example).

If providing a shopping list, don't list commonly stocked ingredients like salt, pepper, flour, sugar, olive oil, vegetable oil, or sesame oil.
"""  # noqa

FRONT_MATTER_TEMPLATE = """---
filename: {filename}
---
"""

TOOLS = [
    {
        'type': 'function',
        'function': {
            'name': 'search_recipes',
            'description': 'Search for existing recipes relevant to the provided query',
            'parameters': {
                'type': 'object',
                'properties': {
                    'query': {
                        'type': 'string',
                        'description': (
                            'Text (e.g., word, phrase, sentence, etc.) describing recipe characteristics of interest '
                            '(e.g., name, ingredients, instructions, cuisine, meal type, etc.).'
                        ),
                    },
                },
                'additionalProperties': False,
                'required': ['query'],
            },
            'strict': True,
        },
    },
]

EMBEDDING_MODEL = 'text-embedding-3-small'
CHAT_MODEL = 'gpt-4o-2024-11-20'

# https://openai.com/api/pricing/
MODELS = {
    'gpt-4o-2024-11-20': {
        'input_token_cost': 2.5 / 1000000,
        'output_token_cost': 10 / 1000000,
    },
    'o3-mini-2025-01-31': {
        'input_token_cost': 1.1 / 1000000,
        'output_token_cost': 4.4 / 1000000,
    },
    'text-embedding-3-large': {
        'input_token_cost': 0.13 / 1000000,
    },
    'text-embedding-3-small': {
        'input_token_cost': 0.02 / 1000000,
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

oai = OpenAI(max_retries=3, timeout=60)


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
    input_cost = res.usage.prompt_tokens * MODELS[res.model]['input_token_cost']
    output_cost = (
        res.usage.completion_tokens * MODELS[res.model]['output_token_cost']
        if hasattr(res.usage, 'completion_tokens')
        else 0
    )

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

    with ThreadPoolExecutor(max_workers=16) as executor:
        futures = {}
        for filename, content in recipes.items():
            future = executor.submit(oai.embeddings.create, model=EMBEDDING_MODEL, input=content)
            futures[future] = filename

        for future in as_completed(futures):
            progress.increment()
            filename = futures[future]

            try:
                res = future.result()
            except:
                logger.exception(f'failed to embed {filename}')
                continue

            embeddings[filename] = {
                'content': recipes[filename],
                'embedding': res.data[0].embedding,
            }
            cost += estimate_cost(res)

    with open('embeddings.json', 'w') as f:
        json.dump(embeddings, f, separators=(',', ':'))

    timer.done()
    logger.info(f'done in {round(timer.latency, 2)}s (cost: ${round(cost, 2)})')


class Toolbox:
    @staticmethod
    def search_recipes(query, n=25):
        res = oai.embeddings.create(
            model=EMBEDDING_MODEL,
            input=query
        )
        query_embedding = res.data[0].embedding

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
        for recipe in recipes[:n]:
            front_matter = FRONT_MATTER_TEMPLATE.format(filename=recipe['filename'])
            content = embeddings[recipe['filename']]['content']
            docs.append(f'{front_matter}\n{content}')

        return '\n\n'.join(docs)


def get_user_name(user_id):
    if user_id not in user_name_cache:
        if user_id.startswith('B'):
            bot_info = slack_app.client.bots_info(bot=user_id)

            # We use these user names to populate the `name` field on OpenAI messages,
            # and they require that it match the pattern ^[a-zA-Z0-9_-]+$
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

    messages = [{
        'role': 'system',
        'content': make_prompt(),
    }]

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
            role = 'assistant'
            user_name = get_user_name(bot_id)

        content = replace_user_mentions(reply['text'])

        messages.append({
            'role': role,
            'name': user_name,
            'content': content,
        })

    if not IS_DEPLOYED:
        messages_str = json.dumps(messages[1:], indent=2)
        logger.info(f'messages (minus system) are:\n{messages_str}')

    completion_loop_timer = Timer()
    completion_count = 0
    cost = 0

    while True:
        completion_count += 1
        logger.info(f'requesting completion {completion_count}')

        completion = oai.chat.completions.create(
            model=CHAT_MODEL,
            messages=messages,
            temperature=0.7,
            tools=TOOLS
        )

        messages.append(completion.choices[0].message.to_dict())
        cost += estimate_cost(completion)

        if completion.choices[0].finish_reason != 'tool_calls':
            break

        for tool_call in completion.choices[0].message.tool_calls:
            logger.info(
                f'tool_call {tool_call.id}: '
                f'call function {tool_call.function.name} with args {tool_call.function.arguments}'
            )

            args = json.loads(tool_call.function.arguments)
            messages.append({
                'role': 'tool',
                'content': getattr(Toolbox, tool_call.function.name)(**args),
                'tool_call_id': tool_call.id,
            })

    completion_loop_timer.done()
    content = completion.choices[0].message.content
    content_with_urls = replace_filenames(content)
    content_as_mrkdwn = to_mrkdwn(content_with_urls)

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
        'completion_loop_latency (s)': round(completion_loop_timer.latency, 2),
        'completion_count': completion_count,
        'cost': round(cost, 2),
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
