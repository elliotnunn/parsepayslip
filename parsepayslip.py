#!/usr/bin/env python3

import json
import re
from dataclasses import dataclass


@dataclass
class String:
    string: str
    x: float
    y: float
    bold: bool


# Headings starting with "~" mean "column might be a bit to the left"
def column_bounds(strings, headings):
    titles = [s.lstrip("~") for s in headings]
    leftedges = [None] * len(headings)

    # Get column title locations
    for string in strings:
        if not string.bold:
            continue

        if string.string in titles:
            leftedges[titles.index(string.string)] = string.x

        if None not in leftedges:
            break

    if None in leftedges:
        raise ValueError("column titles not found")

    # Pass 2: move locations to the left if alignment is left
    ret = []

    for i in range(1, len(headings)):
        prev = leftedges[i - 1]
        this = leftedges[i]

        if headings[i].startswith("~"):
            ret.append((prev + this) / 2)
        else:
            ret.append(this)

    return ret


def get_table(strings, bounds):
    lc = -1
    lasty = 9999999
    rows = []
    for s in strings:
        if s.bold:
            continue

        if s.y < lasty:
            rows.append([None] * (len(bounds) + 1))
            lastcol = -1
            lasty = s.y
        elif s.y > lasty:
            raise ValueError("aberrant cell above previous")

        col = 0
        for left in bounds:
            if s.x >= left:
                col += 1

        if col < lastcol or rows[-1][col] is not None:
            raise ValueError("aberrant cell to left of previous")

        rows[-1][col] = s.string

    # Undo the wrapping of long text rows
    # This is O(n^2) but easy on the eyes
    # Need a manual loop counter because the array will shorten as we go
    i = 0
    while i < len(rows):
        cells = rows[i]
        if all(c is None or c.endswith(" ") for c in cells):
            cells2 = rows[i + 1]
            for j in range(0, len(cells)):
                if cells[j] is not None:
                    cells2[j] = cells[j] + cells2[j]

            del rows[i]

        else:
            i += 1

    return rows


def extract(pdfbinary):
    # One stream per page. This is a hack.
    pagestreams = re.findall(rb"^stream.+?^endstream", pdfbinary, flags=re.DOTALL | re.MULTILINE)

    pagetoks = [tok(stream) for stream in pagestreams]

    # For each page get a list of (font, x, y, string) tuples
    pagestrings = [interpret(tokens) for tokens in pagetoks]

    # Chop the header off page 3 and onwards
    bodypages = pagestrings[1]
    for p in pagestrings[2:]:
        body = False
        for s in p:
            if body:
                bodypages.append(s)
            elif s.string == "Amount" and s.bold:
                body = True

    head = extract_head(pagestrings[0])
    stem, stem_warnings = extract_stem(pagestrings[0])
    body, body_warnings = extract_body(bodypages)

    warnings = stem_warnings + body_warnings

    # Compare the taxed and untaxed lists from the stem and body
    stem_taxed = sum(item["amount"] for item in stem["taxed_earnings"])
    stem_untaxed = sum(item["amount"] for item in stem["untaxed_earnings"])

    body_taxed = sum(item["amount"] for item in body["prior_period_taxed_earnings"])
    body_taxed += sum(item["amount"] for item in body["current_period_taxed_earnings"])
    body_untaxed = sum(item["amount"] for item in body["prior_period_untaxed_earnings"])
    body_untaxed += sum(item["amount"] for item in body["current_period_untaxed_earnings"])

    if stem_taxed != body_taxed:
        warnings.append(f"Taxed income mismatch: stem {stem_taxed} != body {body_taxed}")

    if stem_untaxed != body_untaxed:
        warnings.append(f"Untaxed income mismatch: stem {stem_untaxed} != body {body_untaxed}")

    struct = {"head": head, "stem": stem, "body": body, "warnings": warnings}

    return struct


def extract_head(text):
    struct = {
        "payer": None,
        "payer_abn": None,
        "employee_name": None,
        "employee_id": None,
        "employee_email": None,
        "employee_address": None,
        "full_time_salary": None,
        "period_end_date": None,
        "period_number": None,
        "hss_contact": None,
        "hss_telephone": None,
        "comments": "",
    }

    boldstrings = [s.string if s.bold else None for s in text]

    struct["payer"] = text[0].string
    struct["payer_abn"] = re.search(r"ABN: (\d{11})", text[1].string).group(1)
    struct["employee_name"] = text[boldstrings.index("Name:") + 1].string
    struct["employee_id"] = text[boldstrings.index("Employee Id:") + 1].string
    struct["hss_contact"] = text[boldstrings.index("HSS Contact:") + 1].string
    struct["period_end_date"] = isodate(text[boldstrings.index("Period End Date:") + 1].string)
    struct["hss_telephone"] = text[boldstrings.index("Telephone:") + 1].string
    struct["period_number"] = int(text[boldstrings.index("Period Number:") + 1].string)
    struct["full_time_salary"] = cents(text[boldstrings.index("Full Time Salary:") + 1].string.strip(" $"))
    struct["employee_email"] = text[boldstrings.index("Home Email:") + 1].string.lower()

    address = []
    for string in text[boldstrings.index("Address:") + 1 :]:
        if string.bold:
            break
        address.append(string.string)
    struct["employee_address"] = "\n".join(address)

    y = 99999999
    for string in text[boldstrings.index("COMMENTS") + 1 :]:
        if string.bold:
            break
        elif string.y < y:
            struct["comments"] += "\n" + string.string
        else:
            struct["comments"] += " " + string.string

        y = string.y

    struct["comments"] = struct["comments"].strip()

    return struct


def extract_stem(text):
    schema = [
        (
            "1. TAXED EARNINGS",
            "taxed_earnings",
            True,
            [
                ("~Units", "units_x_100", cents),
                ("~Rate", "rate_x_100", cents),
                ("Description", "description", None),
                ("~Amount", "amount", cents),
            ],
        ),
        (
            "2. UNTAXED EARNINGS",
            "untaxed_earnings",
            True,
            [
                ("~Units", "units_x_100", cents),
                ("~Rate", "rate_x_100", cents),
                ("Description", "description", None),
                ("~Amount", "amount", cents),
            ],
        ),
        (
            "4. TAX",
            "tax",
            True,
            [
                ("Description", "description", None),
                ("~Amount", "amount", cents),
            ],
        ),
        (
            "5. DEDUCTIONS",
            "deductions",
            True,
            [
                ("Description", "description", None),
                ("~Amount", "amount", cents),
            ],
        ),
        (
            "6. SUPERANNUATION",
            "superannuation",
            True,
            [
                ("Description", "description", None),
                ("~Amount", "amount", cents),
            ],
        ),
        (
            "7. NET PAY",
            "net_deleteme",
            False,
            [
                ("~This Pay", "this_pay", cents),
                ("~Year to Date", "ytd", cents),
            ],
        ),
        (
            "DISBURSEMENTS (BANKED)",
            "net",
            False,
            [
                ("Bank", "bank", None),
                ("Account", "account", None),
                ("~Amount", "amount", cents),
            ],
        ),
        (
            "LEAVE",
            "leave",
            False,
            [
                ("Leave Type", "type", None),
                ("~Balance", "balance_x_100", cents),
                ("Calculated", "calculated", None),
            ],
        ),
    ]

    struct = {
        "taxed_earnings_ytd": None,
        "untaxed_earnings_ytd": None,
        "tax_ytd": None,
        "deductions_ytd": None,
        "superannuation_ytd": None,
        "net_ytd": None,
        "taxed_earnings": [],
        "untaxed_earnings": [],
        "tax": [],
        "deductions": [],
        "superannuation": [],
        "net": [],
        "leave": [],
        "net_deleteme": [],  # delete this at the end
    }

    warnings = []

    sections = {}
    title = None
    for s in text:
        if s.bold and re.match(r"^[. 0-9A-Z\(\)]*[A-Z][. 0-9A-Z\(\)]*$", s.string):
            title = s.string
            sections[title] = []
        elif title is not None:
            sections[title].append(s)

    for their_title, my_title, totalled, fields in schema:
        section = sections[their_title]

        bounds = column_bounds(section, [name for (name, *_) in fields])

        table = get_table(section, bounds)

        for row in table:
            rowstruct = {}
            for value, (their_name, my_name, func) in zip(row, fields):
                if func is not None and value is not None:
                    value = func(value)
                rowstruct[my_name] = value

            struct[my_title].append(rowstruct)

        if totalled:
            boldtext = [s.string for s in section if s.bold]
            total, ytd = boldtext[boldtext.index("Total") + 1 :][:2]
            total = cents(total)
            ytd = cents(ytd)

            expect = sum(row["amount"] or 0 for row in struct[my_title])
            if total != expect:
                warnings.append(f"{their_title} total incorrect: expected {expect}, got {total}")

            struct[my_title + "_ytd"] = ytd

    if struct["leave"][-1]["type"] != "Leave balances displayed are subject to audit":
        warnings.append("Last line of leave not where expected")
    else:
        del struct["leave"][-1]

    # The YTD column of "NET_PAY" repeats the same figure over and over
    struct["net_ytd"] = struct["net_deleteme"][0]["ytd"]

    # The "NET PAY" and "DISBURSEMENTS" tables are otherwise drawn from identical data,
    # so verify them
    side1 = [item["this_pay"] for item in struct["net_deleteme"]]
    side2 = [item["amount"] for item in struct["net"]]
    if side1 != side2 and not (side1 == [0] and side2 == []):
        warnings.append("NET PAY does not match DISBURSEMENTS")

    del struct["net_deleteme"]

    taxable = sum(item["amount"] for item in struct["taxed_earnings"])
    untaxed = sum(item["amount"] for item in struct["untaxed_earnings"])
    tax = sum(item["amount"] for item in struct["tax"])
    deduct = sum(item["amount"] for item in struct["deductions"])
    net = sum(item["amount"] for item in struct["net"])

    expect0 = taxable + untaxed - tax - deduct - net

    if expect0 != 0:
        warnings.append(f"{taxable} taxable + {untaxed} untaxed - {tax} tax - {deduct} deduct - {net} net = {expect0}, not zero")

    taxable_ytd = struct["taxed_earnings_ytd"]
    untaxed_ytd = struct["untaxed_earnings_ytd"]
    tax_ytd = struct["tax_ytd"]
    deduct_ytd = struct["deductions_ytd"]
    net_ytd = struct["net_ytd"]

    expect0 = taxable_ytd + untaxed_ytd - tax_ytd - deduct_ytd - net_ytd

    if expect0 != 0:
        warnings.append(f"YTD {taxable_ytd} taxable + {untaxed_ytd} untaxed - {tax_ytd} tax - {deduct_ytd} deduct - {net_ytd} net = {expect0}, not zero")

    return struct, warnings


def extract_body(text):
    schema = [
        ("~Date From", "date_from", isodate),
        ("~Date To", "date_to", isodate),
        ("Description", "description", None),
        ("~Units", "units_x_100", cents),
        ("~Rate", "rate_x_10000", tenthousandths),
        ("~Amount", "amount", cents),
    ]

    struct = {
        "prior_period_taxed_earnings": [],
        "current_period_taxed_earnings": [],
        "prior_period_untaxed_earnings": [],
        "current_period_untaxed_earnings": [],
    }

    warnings = []

    boldtext = [s.string if s.bold else None for s in text]

    bounds = column_bounds(text, [name for (name, *_) in schema])

    for header in ("PRIOR PERIOD TAXED EARNINGS", "CURRENT PERIOD TAXED EARNINGS", "PRIOR PERIOD UNTAXED EARNINGS", "CURRENT PERIOD UNTAXED EARNINGS"):
        myheader = header.replace(" ", "_").lower()
        sectionstart = boldtext.index(header)
        totalstart = boldtext.index("Total", sectionstart)

        table = get_table(text[sectionstart:totalstart], bounds)

        for row in table:
            rowstruct = {}
            for value, (their_name, my_name, func) in zip(row, schema):
                if func is not None and value is not None:
                    value = func(value)
                rowstruct[my_name] = value

            struct[myheader].append(rowstruct)

        # Validate the section total
        expect = sum(item["amount"] for item in struct[myheader])
        got = cents(text[totalstart + 1].string)
        if expect != got:
            warnings.append(f"Body {header} total mismatch: expected {expect}, got {got}")

    # Validate the two other total fields (taxed, untaxed)
    got = cents(text[boldtext.index("Total Taxable Earnings") + 1].string)
    expect = sum(item["amount"] for item in struct["prior_period_taxed_earnings"])
    expect += sum(item["amount"] for item in struct["current_period_taxed_earnings"])
    if expect != got:
        warnings.append(f"Body total taxable earnings list miscalculated: expected {expect}, got {got}")

    got = cents(text[boldtext.index("Total Untaxed Earnings") + 1].string)
    expect = sum(item["amount"] for item in struct["prior_period_untaxed_earnings"])
    expect += sum(item["amount"] for item in struct["current_period_untaxed_earnings"])
    if expect != got:
        warnings.append(f"Body total untaxed earnings mismatch: expected {expect}, got {got}")

    return struct, warnings


def tok(stream):
    """Extract /FontName and (string) tokens

    One day this might become a real PDF tokenizer.
    """
    allowed = []

    toks = re.findall(rb"\((?:\\\)|[^\)])*\)|\S+", stream)

    return [t for t in toks if t.startswith(b"(") or t in (b"/F1", b"/F2") or re.match(rb"[\d\.]+$", t)]


def interpret(tokens):
    """Convert token stream to String objects"""

    strings = []
    x = y = 0
    for t in tokens:
        if t.startswith(b"/F"):
            font = t.decode("ascii")
        elif t.startswith(b"("):
            strings.append(String(string=unescape(t).decode("cp1252"), x=x, y=y, bold=(font == "/F2")))
        elif re.match(b"\d+(\.(\d+)?)?", t):
            x, y = y, float(t)

    return strings


def unescape(pdfstr):
    # \n       | LINE FEED (0Ah) (LF)
    # \r       | CARRIAGE RETURN (0Dh) (CR)
    # \t       | HORIZONTAL TAB (09h) (HT)
    # \b       | BACKSPACE (08h) (BS)
    # \f       | FORM FEED (FF)
    # \(       | LEFT PARENTHESIS (28h)
    # \)       | RIGHT PARENTHESIS (29h)
    # \\       | REVERSE SOLIDUS (5Ch) (Backslash)
    # \ddd     | Character code ddd (octal)

    # Strip ()
    pdfstr = pdfstr[1:-1]

    result = bytearray()
    pdfstr = iter(pdfstr)  # so we can use next(pdfstr)
    for c in pdfstr:
        if c == ord("\\"):
            c2 = next(pdfstr)
            if c2 == "n":
                result.extend(b"\n")
            elif c2 == "r":
                result.extend(b"\r")
            elif c2 == "t":
                result.extend(b"\t")
            elif c2 == "b":
                result.extend(b"\b")
            elif c2 == "f":
                result.extend(b"\f")
            elif c2 == "(":
                result.extend(b"(")
            elif c2 == ")":
                result.extend(b")")
            elif c2 == "\\":
                result.extend(b"\\")
            elif ord("0") <= c2 <= ord("7"):
                c3 = next(pdfstr)
                c4 = next(pdfstr)
                octal = (c2 - ord("0")) * 64 + (c3 - ord("0")) * 64 + (c4 - ord("0"))
                result.append(octal)
            elif c2 == "\n":
                continue  # line continuation
            else:
                result.append(c2)
        else:
            result.append(c)

    return bytes(result)


def cents(string):
    string = string.replace(" ", "").replace(",", "").lstrip("$")

    if "." not in string:
        string += ".00"

    while len(string.partition(".")[2]) < 2:
        string += "0"

    return int(string.replace(".", ""))


def tenthousandths(string):
    string = string.replace(" ", "").replace(",", "")

    if "." not in string:
        string += ".0000"

    while len(string.partition(".")[2]) < 4:
        string += "0"

    return int(string.replace(".", ""))


def isodate(string):
    string = string.strip()  # whitespace
    m = re.match(r"(\d\d)-(\d\d)-(\d\d\d\d)", string)
    return m.group(3) + "-" + m.group(2) + "-" + m.group(1)


# Make the JSON somewhat human-readable
def prettyprint(struct):
    indented = json.dumps(struct, indent=2)

    # Reprint dictionaries that correspond with line items, as single lines
    def sub(m):
        parsed = json.loads(m.group(0))
        if "amount" in parsed or "calculated" in parsed:
            return json.dumps(parsed)
        else:
            return m.group(0)

    # Search for indented dictionaries with no sub-indentation
    return re.sub(r"\{\n( +)\S.*\n(?:\1\S.*\n)+ *}", sub, indented, flags=re.MULTILINE)


USAGE = """
Unauthorised WA Department of Health payslip parser

USAGE:
parsepayslip.py PAYSLIP                 # print JSON to stdout
parsepayslip.py -d [PAYSLIP ...]        # create PAYSLIP.json for each pdf

SCHEMA:
{
  "head": {
    "payer": str,
    "payer_abn": str,
    "employee_name": str,
    "employee_id": str,
    "employee_email": str,
    "employee_address": str,
    "full_time_salary": int,
    "period_end_date": iso8601,
    "period_number": int,
    "hss_contact": str,
    "hss_telephone": str,
    "comments": str
  },
  "stem": {
    "taxed_earnings_ytd": int,
    "untaxed_earnings_ytd": int,
    "tax_ytd": int,
    "deductions_ytd": int,
    "superannuation_ytd": int,
    "net_ytd": int,
    "taxed_earnings": [
      {"units_x_100": int, "rate_x_100": int, "description": str, "amount": int},
      ...
    ],
    "untaxed_earnings": [
      {"units_x_100": int, "rate_x_100": int, "description": str, "amount": int},
      ...
    ],
    "tax": [
      {"description": ..., "amount": int},
      ...
    ],
    "deductions": [
      {"description": ..., "amount": int},
      ...
    ],
    "superannuation": [
      {"description": ..., "amount": int},
      ...
    ],
    "net": [
      {"bank": str, "account": str, "amount": int},
      ...
    ],
    "leave": [
      {"type": str, "balance": int, "calculated": str},
      ...
    ]
  },
  "body": {
    "prior_period_taxed_earnings": [
      {"date_from": iso8601, "date_to": iso8601, "description": str, "units_x_100": int, "rate_x_10000": int, "amount": int},
    ],
    "current_period_taxed_earnings": [
      ...
    ],
    "prior_period_untaxed_earnings": [
      ...
    ],
    "current_period_untaxed_earnings": [
      ...
    ]
  },
  "warnings": [
    str,
    ...
  ]
}""".strip()


if __name__ == "__main__":
    import sys
    import traceback

    if len(sys.argv) == 2 and not sys.argv[1].startswith("-"):
        inputs = [sys.argv[1]]
        outputs = ["/dev/stdout"]
        forgive = False

    elif len(sys.argv) >= 2 and sys.argv[1] == "-d":
        inputs = sys.argv[2:]
        outputs = [p + ".json" for p in inputs]
        forgive = True

    else:
        sys.exit(USAGE)

    for inpath, outpath in zip(inputs, outputs):
        try:
            with open(inpath, "rb") as f:
                if f.read(4) != b"%PDF":
                    print(f"Error: {inpath}: Not a PDF", sys.stderr)
                    if not forgive:
                        sys.exit(1)

                data = f.read()

            struct = extract(data)

            for w in struct["warnings"]:
                print(f"Warning: {inpath}: {w}", file=sys.stderr)

            with open(outpath, "w") as f:
                f.write(prettyprint(struct))

        except Exception as e:
            print(f"Error: {inpath}:", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            if not forgive:
                sys.exit(1)
