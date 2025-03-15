import json
import logging
import os
import re
import time
from datetime import datetime
from logging.config import dictConfig
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, request
from openai import OpenAI
from slack_bolt import App
from slack_bolt.adapter.flask import SlackRequestHandler
from slackstyler import SlackStyler
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

PROMPT_TEMPLATE = """You are chefbot, a culinary assistant. Be serious, brief, and to the point. Don't include unnecessary details or colorful commentary in your responses.

If you're sharing a recipe and you haven't been asked to come up with a new one, stick to the ones provided in <recipes> tags below, referring to them only by their filenames instead of copying the recipe text (and without offering to share the recipe text). However, if you're sharing the text of a new recipe, copy the Markdown format used for the recipes in <recipes> tags. Include at most one recipe in each of your responses.

It is currently {date} and your users are based in Massachusetts. Be aware of this information when formulating your response and use it to make seasonally appropriate suggestions when relevant, but don't announce that you're doing so. For example, you should demonstrate a slight preference for soups and stews in the winter, and a slight preference for fresh vegetables in the spring and summer. You should also have a very slight preference for vegetarian options.

If you're asked to provide a shopping list, don't include commonly stocked ingredients (e.g., salt, pepper, flour, sugar, olive oil, vegetable oil, sesame oil).

<recipes>{recipes}</recipes>
"""  # noqa

FRONT_MATTER_TEMPLATE = """---
filename: {filename}
---
"""

# https://openai.com/api/pricing/
MODEL = 'gpt-4o-2024-11-20'
MODELS = {
    'gpt-4o-2024-11-20': {
        'input_token_cost': 2.5 / 1000000,
        'output_token_cost': 10 / 1000000,
    },
    'o3-mini-2025-01-31': {
        'input_token_cost': 1.1 / 1000000,
        'output_token_cost': 4.4 / 1000000,
    },
}

assert MODEL in MODELS, f'unknown model {MODEL}, add it to MODELS'

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


def make_prompt(write=False):
    recipes = Path('recipes')
    contents = []

    for file in recipes.iterdir():
        if file.suffix == '.md':
            with file.open() as f:
                content = f.read()
                if content:
                    front_matter = FRONT_MATTER_TEMPLATE.format(filename=file.name)
                    contents.append(f'{front_matter}\n{content.strip()}')

    joined_recipes = '\n\n'.join(contents)
    prompt = PROMPT_TEMPLATE.format(date=datetime.now().strftime('%B %d'), recipes=joined_recipes)

    if write:
        with open('prompt.txt', 'w') as f:
            f.write(prompt)

    return prompt


def estimate_cost(res):
    input_cost = res.usage.prompt_tokens * MODELS[res.model]['input_token_cost']
    output_cost = (
        res.usage.completion_tokens * MODELS[res.model]['output_token_cost']
        if hasattr(res.usage, 'completion_tokens')
        else 0
    )

    return input_cost + output_cost


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

    def replacer(match):
        filename = match.group(1)

        return f'https://github.com/rlucioni/recipes/blob/master/recipes/{filename}'

    return re.sub(pattern, replacer, text)


@task
def think(event):
    e2e_timer = Timer()
    logger.info(f'using {MODEL} to handle app mention')

    messages = [{
        'role': 'system',
        'content': make_prompt(write=not IS_DEPLOYED),
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

    completion_timer = Timer()
    completion = oai.chat.completions.create(
        model=MODEL,
        messages=messages,
        temperature=0.7
    )
    completion_timer.done()

    content = completion.choices[0].message.content
    content_with_urls = replace_filenames(content)

    if not IS_DEPLOYED:
        logger.info(f'sending response:\n{content_with_urls}')

    styler = SlackStyler()
    mrkdwn = styler.convert(content_with_urls).strip()

    slack_app.client.chat_postMessage(
        channel=channel_id,
        text=mrkdwn,
        thread_ts=event['ts'],
        unfurl_links=False,
        unfurl_media=False,
    )

    e2e_timer.done()
    stats = {
        'e2e_latency (s)': round(e2e_timer.latency, 2),
        'completion_latency (s)': round(completion_timer.latency, 2),
        'cost': round(estimate_cost(completion), 2),
        'prompt_tokens': completion.usage.prompt_tokens,
        'completion_tokens': completion.usage.completion_tokens,
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
