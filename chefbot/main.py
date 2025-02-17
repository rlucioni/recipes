import logging
import os
from logging.config import dictConfig

from dotenv import load_dotenv
from slack_bolt import App


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
        # 'werkzeug': {
        #     'handlers': ['console'],
        #     'level': 'INFO',
        #     'propagate': False,
        # },
    },
})

logger = logging.getLogger(__name__)

load_dotenv('.env.private')

DEVELOPER_MESSAGE_TEMPLATE = """You're a private chef. The couple you work for has given you examples of their favorite recipes as Markdown below. Help them meal plan, either by using these recipes or thinking of new ones that you think they'd like based on the given examples. Be concise and include no superfluous details. When sharing a recipe, use the format of the included Markdown recipes.

{recipes}
"""  # noqa


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


app = App(
    token=os.environ.get('SLACK_BOT_TOKEN'),
    signing_secret=os.environ.get('SLACK_SIGNING_SECRET')
)


@app.event('app_mention')
def respond_in_thread(event, say):
    ts = event.get('ts')

    say(
        text='yo',
        thread_ts=ts
    )


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 3000))
    app.start(port=port)
