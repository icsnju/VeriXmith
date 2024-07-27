#!/usr/bin/env python3

import json
import sys
from pprint import pprint
from shutil import copyfile

strategy_file = sys.argv[1]
copyfile(strategy_file, f'{strategy_file}.old')

with open(strategy_file, 'r') as fp:
    old = json.load(fp)

conversion_x = old['conversion_x']
conversion_y = old['conversion_y']

for c in (conversion_x, conversion_y):
    for d in c['_translator_chain']['py/tuple']:
        if 'py/id' in d.keys():
            c['_translator_chain']['py/tuple'].remove(d)
            c['_translator_chain']['py/tuple'].append({
                "py/object": "core.translators.klee.KleeSymbolicExecution",
                "policy": None
            })

new = {'py/tuple': [conversion_x, conversion_y]}
# pprint(new)

with open(strategy_file, 'w') as fp:
    json.dump(new, fp)
