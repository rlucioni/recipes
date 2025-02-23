import json
import logging
import os
import random
import re
from logging.config import dictConfig
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, request
from openai import OpenAI
from slack_bolt import App
from slack_bolt.adapter.flask import SlackRequestHandler
from slugify import slugify
from zappa.asynchronous import task
# from slackstyler import SlackStyler


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

load_dotenv('.env.private')

DEVELOPER_MESSAGE_TEMPLATE = """You're a private chef named chefbot. The couple you work for has given you examples of their favorite recipes as Markdown below. Help them meal plan, either by using these recipes or thinking of new ones that you think they'd like based on the given examples. Be concise and include no superfluous details. When sharing a recipe, use the format of the included Markdown recipes.

{recipes}
"""  # noqa

oai = OpenAI(max_retries=3, timeout=60)


def make_prompt(write=False):
    recipes = Path('recipes')
    contents = []

    for file in recipes.iterdir():
        if file.suffix == '.md':
            with file.open() as f:
                content = f.read()

                if content:
                    contents.append(content.strip())

    joined_recipes = '\n\n'.join(contents)
    prompt = DEVELOPER_MESSAGE_TEMPLATE.format(recipes=joined_recipes)

    if write:
        with open('prompt.txt', 'w') as f:
            f.write(prompt)

    return prompt


# https://openai.com/api/pricing/
MODEL = 'o3-mini-2025-01-31'
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


def estimate_cost(res):
    input_cost = res.usage.prompt_tokens * MODELS[res.model]['input_token_cost']
    output_cost = (
        res.usage.completion_tokens * MODELS[res.model]['output_token_cost']
        if hasattr(res.usage, 'completion_tokens')
        else 0
    )

    return input_cost + output_cost


is_deployed = bool(os.environ.get('AWS_LAMBDA_FUNCTION_NAME'))
slack_app = App(
    token=os.environ.get('SLACK_BOT_TOKEN'),
    signing_secret=os.environ.get('SLACK_SIGNING_SECRET'),
    process_before_response=is_deployed
)
user_name_cache = {}


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


@task
def think(event):
    messages = [{
        'role': 'developer',
        'content': make_prompt(),
    }]

    channel_id = event['channel']

    # Messages in a thread will have a thread_ts identifying their parent message.
    # Parent messages (with 0 or more replies) don't have a thread_ts.
    thread_ts = event.get('thread_ts')
    parent_ts = thread_ts if thread_ts else event['ts']

    replies = slack_app.client.conversations_replies(channel=channel_id, ts=parent_ts, limit=1000)

    for reply in replies['messages']:
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

    messages_str = json.dumps(messages[1:], indent=2)
    logger.info(f'using {MODEL} to handle app mention. messages (minus developer) are:\n{messages_str}')

    completion = oai.chat.completions.create(
        model=MODEL,
        messages=messages,
        reasoning_effort='medium',
    )

    content = completion.choices[0].message.content
    # styler = SlackStyler()
    # mrkdwn = styler.convert(content).strip()

    # prompt_tokens = completion.usage.prompt_tokens
    # completion_tokens = completion.usage.completion_tokens
    # reasoning_tokens = completion.usage.completion_tokens_details.reasoning_tokens
    cost = estimate_cost(completion)

    # request_id = completion.request_id
    logger.info(f'sending response:\n{content} (${cost:.4f})')

    slack_app.client.chat_postMessage(
        channel=channel_id,
        # text=mrkdwn,
        text=content,
        thread_ts=event['ts']
    )


CHEFBOT_USER_ID = 'U08E33CEFKK'
thinking_indicators = [
    'mulling it over',
    'stirring up ideas',
    'simmering on a solution',
    'marinating in thought',
    'preheating new plans',
    'blending concepts',
    'sautéing fresh ideas',
    'brewing a breakthrough',
    'whisking up inspiration',
    'cooking up a masterpiece',
    'chopping up fresh ideas',
    'grilling innovative thoughts',
    'baking a new approach',
    'steaming some insights',
    'searing a solution',
    'slicing through challenges',
    'garnishing with genius',
    'roasting creative concepts',
    'spicing up strategies',
    'plating a masterpiece',
]


@slack_app.event('app_mention')
def respond_to_mention(event):
    channel_id = event['channel']
    indicator = random.choice(thinking_indicators)

    slack_app.client.chat_postMessage(
        channel=channel_id,
        text=f'<@{CHEFBOT_USER_ID}> is {indicator}...',
        thread_ts=event['ts']
    )

    think(event)


flask_app = Flask(__name__)
handler = SlackRequestHandler(slack_app)


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
