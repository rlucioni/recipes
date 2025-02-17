import logging
import os
import re
from logging.config import dictConfig

from dotenv import load_dotenv
from openai import OpenAI
from slack_bolt import App
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


# TODO: Message slack on a schedule with menu for the week.
# Allow interaction in a thread (bolt app), both about the menu but also on-demand (at any time).
# Bonus: if there's a really good recipe, open a PR to merge it to the repo.
def make_prompt(write=False):
    paths = [
        'recipes/alkaline-noodles.md',
        'recipes/almond-cake.md',
        'recipes/banana-bread.md',
        'recipes/basque-cheesecake.md',
        'recipes/black-bean-soup.md',
        'recipes/blueberry-muffins.md',
        'recipes/caldo-verde.md',
        'recipes/candied-walnuts.md',
        'recipes/chicken-and-dumplings.md',
        'recipes/chicken-and-pepper-stir-fry.md',
        'recipes/chicken-noodle-soup.md',
        'recipes/chicken-salad.md',
        'recipes/chili-oil.md',
        'recipes/chocolate-chip-cookies.md',
        'recipes/chocolate-ice-cream.md',
        'recipes/chocolate-mug-cake.md',
        'recipes/churros.md',
        'recipes/cioppino.md',
        'recipes/clam-chowder.md',
        'recipes/daikon-rib-soup.md',
        'recipes/dashi.md',
        'recipes/dumplings.md',
        'recipes/egg-and-tomato.md',
        'recipes/financiers.md',
        'recipes/florentines.md',
        'recipes/garlic-shrimp.md',
        'recipes/gazpacho.md',
        'recipes/general-tsos-chicken.md',
        'recipes/granola.md',
        'recipes/lemon-bars.md',
        'recipes/lobster-rolls.md',
        'recipes/miso-soup.md',
        'recipes/nachos.md',
        'recipes/oatmeal-raisin-cookies.md',
        'recipes/orecchiette-with-sausage-and-broccoli-rabe.md',
        'recipes/pan-con-tomate.md',
        'recipes/pan-fried-baozi.md',
        'recipes/panzanella.md',
        'recipes/pho-bo.md',
        'recipes/pie-crust.md',
        'recipes/pizza.md',
        'recipes/pizzelles.md',
        'recipes/pot-beans.md',
        'recipes/potato-salad.md',
        'recipes/pumpkin-pie.md',
        'recipes/ratatouille.md',
        'recipes/red-cooked-fish.md',
        'recipes/red-cooked-ribs.md',
        'recipes/rice-pudding.md',
        'recipes/roasted-chicken.md',
        'recipes/roasted-vegetable.md',
        'recipes/scones.md',
        'recipes/sheet-pan-brats.md',
        'recipes/shoyu-ramen.md',
        'recipes/smashed-cucumber-salad.md',
        'recipes/soft-boiled-eggs.md',
        'recipes/soltero.md',
        'recipes/somen.md',
        'recipes/soondubu-jjigae.md',
        'recipes/spaetzle.md',
        'recipes/sugar-cookies.md',
        'recipes/taiwanese-beef-noodle-soup.md',
        'recipes/thumbprint-cookies.md',
        'recipes/tomato-soup.md',
        'recipes/wontons-in-chili-oil.md',
        'recipes/yeasted-waffles.md',
        'recipes/yuca-fries.md',
    ]

    contents = []
    for path in paths:
        with open(f'../{path}') as f:
            content = f.read()
            contents.append(content.strip())

    recipes = '\n\n'.join(contents)
    # NOTE: o3-mini-2025-01-31, medium reasoning effort works well. Markdown isn't always correct.
    prompt = DEVELOPER_MESSAGE_TEMPLATE.format(recipes=recipes)

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


app = App(
    token=os.environ.get('SLACK_BOT_TOKEN'),
    signing_secret=os.environ.get('SLACK_SIGNING_SECRET')
)
user_name_cache = {}


def get_user_name(user_id):
    if user_id not in user_name_cache:
        user_info = app.client.users_info(user=user_id)
        user_name_cache[user_id] = user_info['user']['real_name']

    return f'@{user_name_cache[user_id]}'


def replace_user_mentions(text):
    pattern = r'<@([A-Z0-9]+)>'

    def replacer(match):
        user_id = match.group(1)
        return get_user_name(user_id)

    return re.sub(pattern, replacer, text)


@app.event('app_mention')
def respond_to_mention(event, say):
    # TODO: gather any previous thread context/messages?
    ts = event['ts']
    user_message = replace_user_mentions(event['text'])

    logger.info(f'using {MODEL} to handle app mention with text: {user_message}')

    messages = [
        {
            'role': 'developer',
            'content': make_prompt(),
        },
        {
            'role': 'user',
            'content': user_message,
        },
    ]

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
    logger.info(f'sending response: {content} (${cost:.4f})')

    say(
        # text=mrkdwn,
        text=content,
        thread_ts=ts
    )


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 3000))
    app.start(port=port)
