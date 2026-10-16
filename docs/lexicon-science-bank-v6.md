# Lexicon science & long-noun bank v6 (2026-10)

## Source and license

The additional `lexicon_source_words_extra5.py` data is a subset of the noun
headwords in [Harrix/Russian-Nouns](https://github.com/Harrix/Russian-Nouns),
`dist/russian_nouns.txt`, licensed under MIT by Sergienko Anton (2018-present).

The source distribution contains 51,300 lines. We took **1,779 distinct words**
not in the first five internal starter packs. Of those, **421** were
prioritized from science/technology/humanities prefixes; the others come from
additional fields of language. Selection uses only alphabetical word forms
and letter counts; no third-party Python library is needed at runtime.

The selection process normalizes е/ё; requires Cyrillic-only, 10–24 characters,
seven different letters, no duplicate letter bags, and excludes obvious
sensitive/inappropriate words. Of the new candidates, **about 1,460** had at
least 38 combinable words in the repository's current static answer set
during development; these numbers measure candidate potential, **not** the
final number of live rounds.

Existing runtime checks remain mandatory: known morphological noun lemma,
moderation rules, 38+ allowed answers, deduplication by letter bag, and
quality-based shortlist limits (up to 2,100 total playable pages). Newly added
words can be rejected or omitted if they fail those checks. The CI regression
test compiles the actual game bank and asserts 1,000+ of the new words remain
playable in the standard test environment.

No new network access, filesystem downloads, environment variables, database
schema changes, or user-facing new commands are introduced. Existing game
score history, rounds, and source cycle state are retained.

## MIT license notice for the source list

Copyright (c) 2018-present Sergienko Anton

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
