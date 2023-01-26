#!/usr/bin/env python3

import json
import re


def extract(pdfbinary):
    # One stream per page. This is a hack.
    pagestreams = re.findall(rb"^stream.+?^endstream", pdfbinary, flags=re.DOTALL | re.MULTILINE)

    pagetoks = [tok(stream) for stream in pagestreams]

    # For each page get a list of (font, x, y, string) tuples
    pagestrings = [interpret(tokens) for tokens in pagetoks]

    # Process each page into a list of (kind, string) tuples, where kind is:
    #   "B" for a bold string (these are always fixed titles)
    #   "1" is non-bold and the first string of a line (i.e. beneath its predecessor)
    #   " " for subsequent non-bold strings
    # This is a good compromise between a structure that preserves the layout,
    # and one that is searchable.
    # The extract_*() functions accept a list like this.
    intermediate = []
    for i, stringlist in enumerate(pagestrings):
        accum = []

        lasty = float("+inf")  # above top of page
        for font, x, y, string in stringlist:
            if font == "/F2":
                accum.append(("B", string))
            elif y < lasty:
                accum.append(("1", string))
            else:
                accum.append((" ", string))

            lasty = y

        intermediate.append(accum)

    # Merge the second and subsequent pages, stripping off the header
    bodypages = []
    for pg in intermediate[1:]:
        bodypages.extend(pg[pg.index(("B", "Amount")) + 1 :])

    struct = {
        "head": extract_head(intermediate[0]),
        "stem": extract_stem(intermediate[0]),
        "body": extract_body(bodypages),
    }

    # Compare the taxed and untaxed lists from the stem and body
    stem_taxed = sum(item["amount"] for item in struct["stem"]["taxed_earnings"])
    stem_untaxed = sum(item["amount"] for item in struct["stem"]["untaxed_earnings"])

    body_taxed = sum(item["amount"] for item in struct["body"]["prior_period_taxed_earnings"])
    body_taxed += sum(item["amount"] for item in struct["body"]["current_period_taxed_earnings"])
    body_untaxed = sum(item["amount"] for item in struct["body"]["prior_period_untaxed_earnings"])
    body_untaxed += sum(item["amount"] for item in struct["body"]["current_period_untaxed_earnings"])

    if stem_taxed != body_taxed:
        raise ValueError(f"Taxed income mismatch: stem {stem_taxed} != body {body_taxed}")

    if stem_untaxed != body_untaxed:
        raise ValueError(f"Untaxed income mismatch: stem {stem_untaxed} != body {body_untaxed}")

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

    struct["payer"] = text[0][1]
    struct["payer_abn"] = re.search(r"ABN: (\d{11})", text[1][1]).group(1)
    struct["employee_name"] = text[text.index(("B", "Name:")) + 1][1]
    struct["employee_id"] = text[text.index(("B", "Employee Id:")) + 1][1]
    struct["hss_contact"] = text[text.index(("B", "HSS Contact:")) + 1][1]
    struct["period_end_date"] = isodate(text[text.index(("B", "Period End Date:")) + 1][1])
    struct["hss_telephone"] = text[text.index(("B", "Telephone:")) + 1][1]
    struct["period_number"] = int(text[text.index(("B", "Period Number:")) + 1][1])
    struct["full_time_salary"] = cents(text[text.index(("B", "Full Time Salary:")) + 1][1].strip(" $"))
    struct["employee_email"] = text[text.index(("B", "Home Email:")) + 1][1].lower()

    address = []
    for flag, string in text[text.index(("B", "Address:")) + 1 :]:
        if flag == "B":
            break
        address.append(string)
    struct["employee_address"] = "\n".join(address)

    for kind, string in text[text.index(("B", "COMMENTS")) + 1 :]:
        if kind == "1":
            struct["comments"] += "\n" + string
        elif kind == " ":
            struct["comments"] += " " + string
        else:
            break

    struct["comments"] = struct["comments"].strip()

    return struct


def extract_stem(text):
    struct = {
        "taxed_earnings": [],
        "taxed_earnings_ytd": None,
        "untaxed_earnings": [],
        "untaxed_earnings_ytd": None,
        "tax": [],
        "tax_ytd": None,
        "deductions": [],
        "deductions_ytd": None,
        "superannuation": [],
        "superannuation_ytd": None,
        "disbursements": [],
        "leave": {},
    }

    for header in ("1. TAXED EARNINGS", "2. UNTAXED EARNINGS", "4. TAX", "5. DEDUCTIONS", "6. SUPERANNUATION", "DISBURSEMENTS (BANKED)", "LEAVE"):
        myheader = header.rpartition(".")[2].partition("(")[0].lower().strip().replace(" ", "_")

        lines = []
        sectionstart = text.index(("B", header))

        # Scan ahead to non-bold text block, and divide it into lines
        while text[sectionstart][0] == "B" and text[sectionstart][1] != "Total":
            sectionstart += 1
        for flag, string in text[sectionstart:]:
            if flag == "1":
                lines.append([string])
            elif flag == " ":
                lines[-1].append(string)
            else:
                break

        for fields in lines:
            brokenline = ""
            if "EARNINGS" in header:
                if len(fields) == 1:
                    brokenline += fields[0] + " "
                elif len(fields) == 2:
                    struct[myheader].append(
                        {
                            "description": brokenline + fields[0],
                            "amount": cents(fields[1]),
                        }
                    )
                    brokenline = ""
                elif len(fields) == 3:
                    struct[myheader].append(
                        {
                            "rate": cents(fields[0]),
                            "description": brokenline + fields[1],
                            "amount": cents(fields[2]),
                        }
                    )
                    brokenline = ""
                elif len(fields) == 4:
                    struct[myheader].append(
                        {
                            "units_x_100": cents(fields[0]),
                            "rate": cents(fields[1]),
                            "description": brokenline + fields[2],
                            "amount": cents(fields[3]),
                        }
                    )
                    brokenline = ""
                else:
                    raise ValueError()

            elif "DISBURSEMENTS" in header:
                if len(fields) == 3:
                    struct[myheader].append(
                        {
                            "bank": fields[0],
                            "account": fields[1],
                            "amount": cents(fields[2]),
                        }
                    )
                elif len(fields) == 2:
                    struct[myheader].append(
                        {
                            "account": fields[0],
                            "amount": cents(fields[1]),
                        }
                    )
                else:
                    raise ValueError("wrong number of disbursement fields")

            elif "LEAVE" in header:
                if len(fields) == 3:
                    struct[myheader][fields[0]] = {
                        "balance": cents(fields[1]),
                        "calculated": fields[2],
                    }
                elif len(fields) == 1:
                    pass  # annoying "Leave balances displayed are subject to audit"
                else:
                    raise ValueError()

            else:
                # This is an unfortunate hack when superannuation acct is blank
                if len(fields) == 1:
                    if re.match(r"^\s*\d+\.\d\d\s*$", fields[0]):
                        struct[myheader].append(
                            {
                                "description": brokenline.rstrip(),
                                "amount": cents(fields[0]),
                            }
                        )
                        brokenline = ""
                    else:
                        brokenline += fields[0] + " "
                elif len(fields) == 2:
                    struct[myheader].append(
                        {
                            "description": brokenline + fields[0],
                            "amount": cents(fields[1]),
                        }
                    )
                    brokenline = ""
                else:
                    raise ValueError()

        # Validate the section totals if applicable
        if "DISBURSEMENTS" not in header and "LEAVE" not in header:
            totalstart = text.index(("B", "Total"), sectionstart)
            theirtotal = cents(text[totalstart + 1][1])
            mytotal = sum(item["amount"] for item in struct[myheader])
            if mytotal != theirtotal:
                raise ValueError(f"{myheader} total incorrect: expected {mytotal}, got {theirtotal}")

            struct[myheader + "_ytd"] = cents(text[totalstart + 2][1])

    expect = sum(item["amount"] for item in struct["taxed_earnings"])
    expect += sum(item["amount"] for item in struct["untaxed_earnings"])
    expect -= sum(item["amount"] for item in struct["tax"])
    expect -= sum(item["amount"] for item in struct["deductions"])
    got = cents(text[text.index(("B", "7. NET PAY")) + 3][1])
    if expect != got:
        raise ValueError(f"7. NET PAY total incorrect: expected {expect}, got {got}")

    expect = struct["taxed_earnings_ytd"]
    expect += struct["untaxed_earnings_ytd"]
    expect -= struct["tax_ytd"]
    expect -= struct["deductions_ytd"]
    got = cents(text[text.index(("B", "7. NET PAY")) + 4][1])
    if expect != got:
        raise ValueError(f"7. NET PAY YTD incorrect: expected {expect}, got {got}")

    return struct


def extract_body(text):
    struct = {
        "prior_period_taxed_earnings": [],
        "current_period_taxed_earnings": [],
        "prior_period_untaxed_earnings": [],
        "current_period_untaxed_earnings": [],
    }

    for header in ("PRIOR PERIOD TAXED EARNINGS", "CURRENT PERIOD TAXED EARNINGS", "PRIOR PERIOD UNTAXED EARNINGS", "CURRENT PERIOD UNTAXED EARNINGS"):
        myheader = header.replace(" ", "_").lower()
        sectionstart = text.index(("B", header))
        totalstart = text.index(("B", "Total"), sectionstart)

        lines = []

        # Scan ahead to non-bold text block, and divide it into lines
        for flag, string in text[sectionstart:totalstart]:
            if flag == "1":
                lines.append([string])
            elif flag == " ":
                lines[-1].append(string)

        # date_from, date_to, description, units, rate, amount
        for fields in lines:
            if len(fields) == 3:
                struct[myheader].append(
                    {
                        "date_from": isodate(fields[0]),
                        "description": fields[1],
                        "amount": cents(fields[2]),
                    }
                )
            elif len(fields) == 4:
                struct[myheader].append(
                    {
                        "date_from": isodate(fields[0]),
                        "date_to": isodate(fields[1]),
                        "description": fields[2],
                        "amount": cents(fields[3]),
                    }
                )
            elif len(fields) == 5:
                struct[myheader].append(
                    {
                        "date_from": isodate(fields[0]),
                        "description": fields[1],
                        "units_x_100": cents(fields[2]),
                        "rate_x_10000": tenthousandths(fields[3]),
                        "amount": cents(fields[4]),
                    }
                )
            elif len(fields) == 6:
                struct[myheader].append(
                    {
                        "date_from": isodate(fields[0]),
                        "date_to": isodate(fields[1]),
                        "description": fields[2],
                        "units_x_100": cents(fields[3]),
                        "rate_x_10000": tenthousandths(fields[4]),
                        "amount": cents(fields[5]),
                    }
                )

        # Validate the section total
        expect = sum(item["amount"] for item in struct[myheader])
        got = cents(text[totalstart + 1][1])
        if expect != got:
            raise ValueError(f"Body {header} total mismatch: expected {expect}, got {got}")

    # Validate the two other total fields (taxed, untaxed)
    got = cents(text[text.index(("B", "Total Taxable Earnings")) + 1][1])
    expect = sum(item["amount"] for item in struct["prior_period_taxed_earnings"])
    expect += sum(item["amount"] for item in struct["current_period_taxed_earnings"])
    if expect != got:
        raise ValueError(f"Body total taxable earnings list miscalculated: expected {expect}, got {got}")

    got = cents(text[text.index(("B", "Total Untaxed Earnings")) + 1][1])
    expect = sum(item["amount"] for item in struct["prior_period_untaxed_earnings"])
    expect += sum(item["amount"] for item in struct["current_period_untaxed_earnings"])
    if expect != got:
        raise ValueError(f"Body total untaxed earnings mismatch: expected {expect}, got {got}")

    return struct


def tok(stream):
    """Extract /FontName and (string) tokens

    One day this might become a real PDF tokenizer.
    """
    allowed = []

    toks = re.findall(rb"\((?:\\\)|[^\)])*\)|\S+", stream)

    return [t for t in toks if t.startswith(b"(") or t in (b"/F1", b"/F2") or re.match(rb"[\d\.]+$", t)]


def interpret(tokens):
    """Convert token stream to a list of font,x,y,string tuples"""

    strings = []
    x = y = 0
    for t in tokens:
        if t.startswith(b"/F"):
            font = t.decode("ascii")
        elif t.startswith(b"("):
            strings.append((font, x, y, unescape(t).decode("cp1252")))
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
    "payer": string,
    "payer_abn": string,
    "employee_name": string,
    "employee_id": string,
    "employee_email": string,
    "employee_address": string,
    "full_time_salary": int,
    "period_end_date": is8601,
    "period_number": int,
    "hss_contact": string,
    "hss_telephone": string,
    "comments": int
  },
  "stem": {
    "taxed_earnings": [
      {"units_x_100": int, "rate": int, "description": string, "amount": int},
      ...
    ],
    "taxed_earnings_ytd": int,
    "untaxed_earnings": [
      {"units_x_100": int, "rate": int, "description": string, "amount": int},
      ...
    ],
    "untaxed_earnings_ytd": int,
    "tax": [
      {"description": string, "amount": int},
      ...
    ],
    "tax_ytd": int,
    "deductions": [
      {"description": string, "amount": int},
      ...
    ],
    "deductions_ytd": int,
    "superannuation": [
      {"description": string, "amount": int},
      ...
    ],
    "superannuation_ytd": int,
    "disbursements": [
      {"bank": string, "account": string, "amount": int},
      ...
    ],
    "leave": {
      string: {"balance": int, "calculated": string},
      ...
    }
  },
  "body": {
    "prior_period_taxed_earnings": [
      {"date_from": "yyyy-mm-dd", "date_to": "yyyy-mm-dd", "description": string, "units_x_100": int, "rate_x_10000": int, "amount": int},
      ...
    ],
    "current_period_taxed_earnings": [
      {"date_from": "yyyy-mm-dd", "date_to": "yyyy-mm-dd", "description": string, "units_x_100": int, "rate_x_10000": int, "amount": int},
      ...
    ],
    "prior_period_untaxed_earnings": [
      {"date_from": "yyyy-mm-dd", "date_to": "yyyy-mm-dd", "description": string, "units_x_100": int, "rate_x_10000": int, "amount": int},
      ...
    ],
    "current_period_untaxed_earnings": [
      {"date_from": "yyyy-mm-dd", "date_to": "yyyy-mm-dd", "description": string, "units_x_100": int, "rate_x_10000": int, "amount": int},
      ...
    ]
  }
}
""".strip()

if __name__ == "__main__":
    import sys

    if len(sys.argv) == 2 and not sys.argv[1].startswith("-"):
        with open(sys.argv[1], "rb") as f:
            if f.read(4) != b"%PDF":
                sys.exit(path + ": not a PDF")
            f.seek(0)
            print(prettyprint(extract(f.read())))

    elif len(sys.argv) >= 2 and sys.argv[1] == "-d":
        for path in sys.argv[2:]:
            try:
                with open(path, "rb") as f, open(path + ".json", "w") as out:
                    if f.read(4) != b"%PDF":
                        print(path + ": not a PDF", sys.stderr)
                    f.seek(0)
                    print(prettyprint(extract(f.read())), file=out)
            except Exception as e:
                print(path + ": " + str(e), file=sys.stderr)

    else:
        sys.exit(USAGE)
