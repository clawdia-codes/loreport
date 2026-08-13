#!/usr/bin/env python3
"""
hub/screen_calibrate.py — does the Pass 3 critic actually discriminate?

A screen that keeps everything and a screen that kills everything are equally useless,
and both look like a working pipeline from the outside: one yields "0 survivors, the
corpus was thin", the other yields "164 survivors, what a haul". Neither number tells you
whether any judgement happened.

This measures it. It feeds the critic a fixed set of drafts whose correct verdict is
already known and reports the separation:

    separation = (GOOD kept) - (BAD kept)

4/4 is a perfect screen. 0/4 is no screen at all, in whichever direction. Run this before
trusting any Pass 3 output, and after any change to the critic prompt or model.

MEASURED ON 2026-08-13 — BOTH LOCAL MODELS SCORED 0/4
------------------------------------------------------
    prompt variant            model            GOOD kept   BAD kept   separation
    adversarial ("default     gemma4:latest        0/4        0/4         0
      to kill if unsure")     nemotron3:33b        0/4        0/4         0
    balanced (KEEP test       gemma4:latest        4/4        4/4         0
      + REJECT test)          nemotron3:33b        4/4        4/4         0

The adversarial variant rejected four memories the user has accepted and uses daily,
including `user-timezone`. The balanced variant accepted four drafts that claim "values
plain-language summaries" on the evidence of a question about an iPhone camera. The
wording was not the problem: neither local model can make this distinction, so Pass 3
must not be trusted on them regardless of how its prompt is phrased.

THE FIXTURES ARE SYNTHETIC, AND THAT IS NOT AN ACCIDENT
--------------------------------------------------------
This is the PUBLIC engine repo. The first version of this file used a real brain's
memories verbatim, including a family member's name and date of birth, and it sailed
through `tests/test_no_personal_identifiers.py` because that guard matches the instance
owner's own name and home directory, not the names of people around them. A calibration
fixture needs items whose correct verdict is known — it does not need them to be true, so
there is no reason to pay that risk.

GOOD cases are shaped like durable memories that plainly follow from their quotes. BAD
cases are shaped like the real failure this screen exists to catch: a broad trait claimed
on the evidence of an unrelated question. Keep both synthetic.

An earlier version of the fixture used each item's own description as its quote; that is
circular, "unsupported" was a fair verdict on it, and the test proved nothing. Quotes
must support the claim without restating it.
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import screen  # noqa: E402


def _draft(name, dtype, description, body, quotes, label):
    return {
        "name": name, "type": dtype, "description": description, "body": body,
        "_label": label,
        "stability": {"evidence": 3, "has_directive": True, "span_days": 200,
                      "first_seen": "2026-01-01", "last_seen": "2026-08-01",
                      "sources": ["claude", "chatgpt"]},
        "evidence_quotes": [{"quote": q, "source": "chatgpt", "date": "2026-05-01"}
                            for q in quotes],
    }


GOOD = [
    _draft("feedback-bottom-line-first", "feedback",
           "Wants a plain-language bottom line before the detail; dense status writing "
           "loses them even when it is correct.",
           "They have asked for the summary up front more than once.",
           ["give me the bottom line first, then the detail",
            "this is too dense, I can't follow it"], "GOOD"),
    _draft("user-workday-timezone", "user",
           "Works in the Europe/Lisbon timezone.",
           "Scheduling and timestamps should assume Europe/Lisbon.",
           ["I'm in Lisbon, so 0800 my time",
            "it's nearly midnight here, I'm heading to bed"], "GOOD"),
    _draft("feedback-check-before-claiming", "feedback",
           "Do not claim a feature exists or a setting is applied without checking it "
           "with the relevant tool first.",
           "They have corrected confident unverified claims more than once.",
           ["did you actually run that, or are you guessing?",
            "show me the test output before you tell me it works"], "GOOD"),
    _draft("person-colleague-sam", "person",
           "Sam is their co-founder and handles the commercial side.",
           "Sam owns pricing and customer conversations; they own the product.",
           ["Sam handles all the pricing decisions, I stay on product"], "GOOD"),
]

# Real drafts from the 2026-08-13 Pass 2 run. Each states a broad trait on the evidence
# of a quote that has nothing to do with it — the Barnum failure the screen exists for.
BAD = [
    _draft("user-profile-a", "person",
           "The user is a professional who values concise plain-language summaries and "
           "detailed hardware compatibility checks.",
           "Inferred from their questions.",
           ["Does the new phone have a wide angle camera?"], "BAD"),
    _draft("user-profile-b", "user",
           "The user is a technical professional who values plain-language summaries "
           "and hates dense jargon.",
           "Inferred from their questions.",
           ["What's the difference between an operator and a fragment?"], "BAD"),
    _draft("user-profile-c", "user",
           "The user has a strong preference for plain-language summaries and dislikes "
           "dense technical writing.",
           "Inferred from their questions.",
           ["Can you give me the ranking of the sum of two dice?"], "BAD"),
    _draft("report-at-every-milestone", "feedback",
           "The user demands detailed reporting at each sprint milestone.",
           "A standing reporting instruction.",
           ["REPORT at every milestone: run report.sh log then report.sh show "
            "(per the plan's Reporting standing instruction)"], "BAD"),
]


def main():
    ap = argparse.ArgumentParser(description="Measure Pass 3 critic separation.")
    ap.add_argument("--model", default=screen.DEFAULT_MODEL)
    ap.add_argument("--endpoint", default=screen.DEFAULT_ENDPOINT)
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--min-separation", type=int, default=3,
                    help="exit 1 below this (default 3 of 4)")
    args = ap.parse_args()

    good_kept = bad_kept = 0
    for d in GOOD + BAD:
        v = screen.screen_one(d, args.model, args.endpoint, args.timeout)
        kept = bool(v) and (v.get("verdict") or "").lower() == "keep"
        mark = "keep" if kept else "kill"
        expect = "keep" if d["_label"] == "GOOD" else "kill"
        flag = " " if mark == expect else "  <-- WRONG"
        print(f"  [{d['_label']:4}] {d['name']:38} -> {mark}{flag}")
        if kept and d["_label"] == "GOOD":
            good_kept += 1
        if kept and d["_label"] == "BAD":
            bad_kept += 1

    sep = good_kept - bad_kept
    print(f"\nmodel:       {args.model}")
    print(f"GOOD kept:   {good_kept}/{len(GOOD)}")
    print(f"BAD kept:    {bad_kept}/{len(BAD)}")
    print(f"separation:  {sep}/{len(GOOD)}")
    if sep < args.min_separation:
        print("VERDICT: this critic does not discriminate. Pass 3 output is meaningless "
              "with this model — a uniform answer is not a screen.")
        return 1
    print("VERDICT: the critic separates good from bad.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
