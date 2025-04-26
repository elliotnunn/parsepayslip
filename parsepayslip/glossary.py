#!/usr/bin/env python3

# Copyright (c) 2023 Elliot Nunn
# Licensed under the MIT license

USAGE = """
Department of Health payslips summarise earnings on page 1, but the line items
are savagely abbreviated. This script creates a glossary of abbreviations from
your library of payslips.

Convert your payslips to JSON using parsepayslip.py, then run:

parseglossary PAYSLIP.json ...
""".strip()


def glossary(payslips):
    pairs = set()
    for struct in payslips:
        for taxedness in ["taxed", "untaxed"]:
            # Page 1: lines are already added up
            shortside = {}
            for line in struct["stem"][f"{taxedness}_earnings"]:
                amount = line["amount"]
                desc = line["description"]
                shortside.setdefault(amount, []).append(desc)

            # Page 2: add the lines up
            sums = {}
            for priorness in ["prior", "current"]:
                for line in struct["body"][f"{priorness}_period_{taxedness}_earnings"]:
                    amount = line["amount"]
                    desc = line["description"]
                    sums.setdefault(desc, 0)
                    sums[desc] += amount

            # longside and shortside are {amount: [description, ...], ...}
            longside = {}
            for desc, amount in sums.items():
                longside.setdefault(amount, []).append(desc)

            # Get unambiguous short <-> long mappings
            for amount in set(shortside).intersection(set(longside)):
                shorts = shortside[amount]
                longs = longside[amount]

                if len(shorts) == len(longs) == 1:
                    pairs.add((shorts[0], longs[0]))

    # many <-> many relationship
    db = []
    for short, long in pairs:
        idx = set()
        for i, (exist_longs, exist_shorts) in enumerate(db):
            if long in exist_longs:
                idx.add(i)
            if short in exist_shorts:
                idx.add(i)

        if len(idx) == 0:
            db.append(({long}, {short}))
        elif len(idx) == 1:
            longset, shortset = db[next(iter(idx))]
            longset.add(long)
            shortset.add(short)
        else:
            raise ValueError(f"Pair ({long})({short}) matches too many")

    return db


if __name__ == "__main__":
    import sys
    import json

    if len(sys.argv) < 2:
        sys.exit(USAGE)

    structs = [json.load(open(f)) for f in sys.argv[1:]]

    db = glossary(structs)

    lines = []
    for longs, shorts in db:
        lines.append((" | ".join(sorted(shorts)), " | ".join(sorted(longs))))

    lines.sort()

    col = max(len(l[0]) for l in lines)

    for s, l in lines:
        print(s.rjust(col) + " = " + l)
