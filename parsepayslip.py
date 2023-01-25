#!/usr/bin/env python3

import json
import re
import sys


header_mapping = {
    # left column
    "Name": "employee_name",
    "HSS Contact": "hss_contact",
    "Telephone": "hss_telephone",
    # right column
    "Employee Id": "employee_id",
    "Period End Date": "period_end_date",
    "Period Number": "period_number",
    "Full Time Salary": "full_time_salary",
    # right column, first page only
    "Home Email": "employee_email",
    "Address": "employee_address",
}


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
    return pdfstr


def dump(pdf):
    pdf = iter(pdf)

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
    }

    if not next(pdf).startswith("%PDF"):
        print(pdf.name() + ": not a PDF", file=sys.stdout)
        return

    mode = "start"

    for line in pdf:
        line = line.rstrip("\r\n")  # (?:[^\)]|\\\))

        # A line always (conveniently) heralds a new section
        if line.endswith(" l S"):
            mode = "new-section"

        elif mode == "start":
            if m := re.match(r"^[^\(]*\(((?:[^\)]|\\\))+)\)", line):
                struct["payer"] = unescape(m.group(1))
                mode = "await-abn"

        elif mode == "await-abn":
            if m := re.match(r"^[^\(]*\(ABN: ((?:[^\)]|\\\))+)\)", line):
                struct["payer_abn"] = unescape(m.group(1))
                mode = "await-key"

        elif mode == "await-key":
            regex = r"^[^\(]*\((FIELD):\)".replace("FIELD", "|".join(header_mapping))
            if m := re.match(regex, line):
                currentkey = header_mapping[m.group(1)]
                mode = "await-value"

        elif mode == "await-value":
            if m := re.match(r"^[^\(]*\(((?:[^\)]|\\\))+)\)", line):
                value = unescape(m.group(1))

                # reformat a few values:
                if currentkey == "employee_address":
                    if struct[currentkey] is None:
                        struct[currentkey] = value
                    else:
                        struct[currentkey] += "\n" + value
                    mode = "await-value" # more address lines

                elif currentkey == "full_time_salary":  # to integer cents
                    value = re.sub(r"[^\d\.]", "", value)
                    if value[-3] != ".":
                        raise ValueError("badly formatted full time salary")

                    struct[currentkey] = int(value.replace(".", ""))
                    mode = "await-key"

                elif currentkey == "period_end_date":
                    m = re.match(r"^(\d\d)-(\d\d)-(\d\d\d\d)$", value)

                    struct[currentkey] = m.group(3) + "-" + m.group(2) + "-" + m.group(1)
                    mode = "await-key"

                elif currentkey == "period_number":
                    struct[currentkey] = int(value)
                    mode = "await-key"

                else:
                    struct[currentkey] = value
                    mode = "await-key"


    print(json.dumps(struct, indent=2))


#       if m := re.match(r'^[^\(]*\((Name|Employee Id|):\)', line):
#           print(line)

if __name__ == "__main__":
    import sys

    for path in sys.argv[1:]:
        with open(path, encoding="cp1252") as file:
            dump(file)
