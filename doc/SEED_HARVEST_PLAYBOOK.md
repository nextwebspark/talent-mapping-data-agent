# Seed Harvest Playbook (GCC)

Operating manual for harvesting a seed company list per (sector, country) using **Claude Code's WebSearch + WebFetch tools only** (no Gemini, no PDL). Output goes to `public.company_seed_list`.

This playbook is meant to be read at the start of every harvest chat session. The Python codebase exposes the storage layer (`src/tools/supabase_tool.py`); Claude Code performs the harvesting in-session.

## Scope

- GCC 6 only: `United Arab Emirates`, `Saudi Arabia`, `Qatar`, `Kuwait`, `Bahrain`, `Oman`.
- Sectors: the 20 entries in [src/agent/taxonomy.py](src/agent/taxonomy.py) `SECTORS`.
- Target per (sector, country): up to 200 unique company slugs. Realistic ceiling is 50–150 for large sectors, less for niche ones.
- **Same company can legitimately live in multiple sectors.** The unique key is `(slug, country, sector, harvest_version)`, so the same `slug` appearing under e.g. `Banking & Financial Services` *and* `Capital Markets & Asset Management` produces two rows and is correct. Do not try to dedup across sectors in your buffer. Dedup only against `fetch_seed_slugs(country, sector)` for the **current** sector being harvested.

## Invocation

Start a fresh Claude Code chat and prompt, e.g.:

> Harvest the seed list for `sector="Banking & Financial Services"` and `country="United Arab Emirates"`. Follow `doc/SEED_HARVEST_PLAYBOOK.md`.

## Session loop

1. **Bootstrap.** Call `fetch_seed_slugs(country, sector)` once. Keep result as `seen_slugs: set[str]` in scratch memory.
2. **Search.** Run `WebSearch` with templated queries (below). Collect candidate result URLs ranked by source allowlist.
3. **Fetch.** For each high-value URL, call `WebFetch` with a focused prompt like: *"List every distinct company name that appears in a banking/financial-services list for the UAE **on this page only**. For each, return name and website if present. Skip individuals and generic mentions. Do not add companies you remember from training; only quote what is on the page."*
4. **Verify before buffering.** Only buffer a candidate if its name was literally returned by a `WebFetch` (or appeared in a `WebSearch` snippet) in this session. Cross-check the name back to the result text. Drop anything you cannot point to a quote for.
5. **Normalise.** Compute `slug = slugify(name)` (use [src/tools/supabase_tool.py:slugify](src/tools/supabase_tool.py)). Drop if `slug in seen_slugs`. Otherwise add to `seen_slugs` and a batch buffer.
6. **Persist.** Every 20 rows in the buffer, call `write_seed_companies(batch)`. Drain the buffer on stop conditions too.
7. **Stop** when any of: `len(seen_slugs) >= 200`, **or** 3 consecutive search queries yield <5 net-new slugs, **or** the token budget for the session is approaching its limit.

### Anti-hallucination guardrails (re-read every session)

- Never list a company from memory. If the only source is "I know X exists in the UAE", do not write the row.
- Never fabricate a `source_url`. If you cannot attribute a name to a real fetched page, drop it.
- Never invent a `website`. Leave it null when the source page does not show one. Do not guess `https://www.<name>.com`.
- Do not paraphrase or pluralise names to make them look canonical (e.g. don't expand "ADCB" to "Abu Dhabi Capital Bank" if the source says only "ADCB").
- If a `WebFetch` response itself looks invented (model speculating, no real list on the page), discard everything from that fetch.

## WebSearch query templates

Replace `{sector}` with the taxonomy sector and `{country}` with the country. Run them in roughly this order; later queries are for the long tail.

1. `top {sector} companies in {country} 2025`
2. `largest {sector} companies {country} list`
3. `Forbes Middle East {sector} {country}`
4. `Zawya {country} {sector} directory`
5. `{country} {sector} chamber of commerce members`
6. `{country} stock exchange listed {sector}`
7. `Gulf Business top {country} {sector}`
8. `{country} {sector} association members`
9. `Arabian Business top {country} {sector}`

For sector-specific seed phrasing also try synonyms:
- `Banking & Financial Services` → `banks`, `Islamic banks`, `wealth management firms`
- `Oil & Gas - Upstream` → `oil companies`, `upstream operators`, `E&P firms`
- `Real Estate Development` → `property developers`, `master developers`
- `Hospitality, Travel & Tourism` → `hotel groups`, `hospitality operators`, `tour operators`
- `Telecommunications` → `telecom operators`, `ISPs`, `tower companies`

## Source allowlist (prefer)

- `forbesmiddleeast.com`
- `zawya.com`
- `arabianbusiness.com`
- `gulfbusiness.com`
- `meed.com`
- Official stock exchange listings: `dfm.ae`, `adx.ae`, `tadawul.com.sa`, `dsm.com.qa`, `boursakuwait.com.kw`, `bahrainbourse.com`, `mse.om`
- Chamber of commerce member directories (`.gov.*` / official chamber sites)
- Sector regulator sites (e.g. central banks, ministry pages)

## Source denylist (skip)

- LinkedIn personal profiles, generic LinkedIn company pages without context
- Pinterest, Quora, Reddit, generic blogspot pages
- Press releases that name only one company
- Job-board listings (Bayt, GulfTalent) unless they expose a clear employer directory
- Sites whose snippets are clearly AI-generated SEO spam (lists of "best X" without sources)

## Parsing rules

- **No invented companies.** Only write a row if the company name appears verbatim on a page fetched in this session via `WebSearch` or `WebFetch`. If you cannot quote the name from a specific fetched URL, do not write it. Inferred-but-unverified names ("there must be a bank called X"), training-knowledge recalls, and bridge-completions are all forbidden.
- **One source URL per row** must be the exact page the name was read from. If a name appears in multiple sources, pick the first source where you saw it — never fabricate or generalise the URL.
- Extract proper nouns that are clearly company names: trading style + entity suffix (LLC, PJSC, Holding, Group, Co.) when present.
- Reject generic mentions ("a leading bank", "major retail chain", "several developers").
- Reject government ministries unless the sector matches a state-owned operator (e.g. ADNOC in Oil & Gas).
- If the page lists a parent group plus subsidiaries that are themselves notable companies, capture both — they will likely be deduped by slug anyway.
- Capture `website` only when explicitly present on the same page; never guess. If unsure whether a URL is real, leave `website` null rather than infer.
- Capture `source_title` from the page title; `source_query` from the `WebSearch` query that surfaced it. Both must be the literal strings used, not paraphrased.

## Stop conditions

- 200 unique slugs reached.
- 3 consecutive WebSearch calls yielded <5 net-new slugs.
- The session has used a noticeable share of its token budget — flush the buffer and stop rather than enter another long fetch cycle.

## Write cadence

Batch via `write_seed_companies(rows)` every 20 buffered candidates. Final flush at end-of-session, no matter how few rows. Each call is upserted on `(slug, country, sector, harvest_version)` — re-running the same pair is safe.

## After the session

Report back to the user:
- Pair (sector, country) harvested.
- New rows written this session, total slugs now in the table.
- Sources that produced most rows (helps tune allowlist).
- Suggested adjacent (sector, country) pair to run next.

SQL to verify:

```sql
select count(*) as total, count(distinct slug) as unique_slugs
from   company_seed_list
where  country = 'United Arab Emirates'
  and  sector  = 'Banking & Financial Services';

select name, website, source_url
from   company_seed_list
where  country = 'United Arab Emirates'
  and  sector  = 'Banking & Financial Services'
order by random() limit 20;
```
