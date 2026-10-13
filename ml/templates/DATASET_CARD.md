# Dataset card — `<dataset name>`

> Template (Day 31). Copy to the dataset's directory, fill every field, and
> delete the guidance in italics. A field that does not apply says **n/a** with a
> reason — never left blank. If any of the last three sections is incomplete, the
> dataset may not be trained on (AGENTS.md, ML ground rule 4/9).

| | |
| --- | --- |
| Card version | `0.1` (`<date>`) |
| Status | `planned \| collecting \| review \| frozen \| retired` |
| Catalogue entry | `ml/data/CATALOGUE.md` § `<n>` |
| Owner / authors | `<who>` |
| Licence | `<licence + how a user of the data must comply>` |
| Licence evidence | `<URL read, and date read>` |
| Last verified | `<YYYY-MM-DD>` (must be < 180 days old; `make ml-catalogue` checks this) |

## 1. What it is

_Three sentences: what the data is, what task it supports, and what it is
explicitly not for._

## 2. Provenance

| | |
| --- | --- |
| Origin | `<scraped / crowdworkers / authors / translated / synthetic>` |
| Collection date | `<when>` |
| Collection method | `<how, with the exact command or script if it was a download>` |
| Platform terms | `<if the text came from a platform: which terms apply and how they were satisfied — or n/a>` |
| Personal data | `yes \| no \| possible` — `<what kind, and what was done about it>` |
| Consent basis | `<what the people whose words these are agreed to>` |
| Redistribution | `<may these rows be shipped? usually no — then DVC only, never Git>` |

## 3. Contents

| | |
| --- | --- |
| Languages | `<codes, and the register of each>` |
| Size | `<rows, bytes, splits>` |
| Label scheme | `<labels, definitions, who decided them>` |
| Label quality | `<inter-annotator agreement, review process, or "none — synthetic">` |
| Fields | `<name: type — meaning>` |
| Hashes | `<where the sha256/DVC hash is recorded (manifest, dvc.lock, run id)>` |

## 4. Known biases

_Be specific, not apologetic. Each of these becomes a caveat in any model card
that uses this data._

- `<sampling bias: who is over/under-represented>`
- `<register bias: how these words differ from how Manovia's users write>`
- `<label bias: which labels absorb others, which are missing entirely>`
- `<temporal/language drift: what would make this stale>`

## 5. Fitness for Manovia

| use | allowed? | notes |
| --- | --- | --- |
| Training a shipped model | `<yes / no / with conditions>` | `<...>` |
| Evaluation / benchmark | | |
| Research comparison only | | |
| Committed to Git | `no` (datasets never are) | `<what is committed instead: hash, stats, card>` |

## 6. Handling

- Storage: `<DVC cache / external path; never inside the repository tree>`
- Retention: `<when the download is deleted, and by whom>`
- Deletion: `<command or step, so it is not a guess at audit time>`
- Access: `<who can read it, where credentials are allowed to live (never in the repo)>`

## 7. Splits

| split | rows | how chosen | may it be changed? |
| --- | --- | --- | --- |
| train | | `<seeded stratification, seed value>` | yes, with a new hash and a re-recorded baseline |
| eval | | | yes, loud |
| test | | | **frozen** — hash pinned, threshold tuning forbidden against it |

## 8. Review record

_Two independent native speakers per language for Indic sets; a domain expert for
any clinical-adjacent content. Reviewer identities are recorded as initials only._

| reviewer | role | scope | date | outcome |
| --- | --- | --- | --- | --- |
| `<initials>` | `<native speaker (hi)>` | `<all hi rows>` | | `<accepted / n rows changed>` |

## 9. Sign-off

| check | who | date |
| --- | --- | --- |
| Catalogue entry complete and verified | | |
| Licence read at source (not a mirror) | | |
| Personal-data statement true | | |
| No rows in Git (`git ls-files` checked) | | |
| Card complete before first training run | | |
