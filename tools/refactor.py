#!/usr/bin/env python3
"""
Utility for refactoring recipes.

Run from the root of the project with
    
    $ ./tools/refactor.py
"""
import os


DIRECTORIES = ['drink', 'food']
INGREDIENTS = '### Ingredients\n\n'
INSTRUCTIONS = '### Instructions\n\n'


def insert_headings(f):
    lines = f.readlines()
    if lines and lines[0].startswith('#'):
        ingredients = []
        instructions = []

        for index, line in enumerate(lines):
            if line.startswith('- '):
                ingredients.append((index, line))
            elif line[0].isdigit and line[1:3] == '. ':
                instructions.append((index, line))
                break

        lines.insert(ingredients[0][0], INGREDIENTS)
        # Need to add 1 to account for the string we're inserting above.
        lines.insert(instructions[0][0] + 1, INSTRUCTIONS)

        f.seek(0)
        f.writelines(lines)


for directory in DIRECTORIES:
    filenames = os.listdir('recipes/' + directory)

    for filename in filenames:
        if filename.endswith('.md'):
            with open('recipes/{}/{}'.format(directory, filename), 'r+') as f:
                insert_headings(f)
