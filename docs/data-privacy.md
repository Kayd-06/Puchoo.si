# Data privacy: what leaves Puchoo.si

This page lists every outside service Puchoo.si talks to and exactly what it
sends. It also lists what goes into the server logs.

## Outside services

| Service | When | What is sent | What is never sent |
| --- | --- | --- | --- |
| DeepSeek (SQL planner and generator) | Every question | The question (in English), the schema: table names, column names and types, join hints, and short categorical hints such as `voucher_type: 'Sales'` | Rows, cell values, uploaded files, query results, report headers (company name, address, phone, email) |
| DeepSeek or Anthropic (verifier) | After a query runs | The question, the SQL with its literals, the result's column names, a value kind per column (number/text) and the row count | Result rows. They are sent only if `PUCHOO_LLM_SHARE_RESULT_ROWS=1` |
| Sarvam (translation) | Non-English questions and answers | The question, fixed UI labels, column labels, the headline label | Result cell text, unless `PUCHOO_TRANSLATE_RESULT_VALUES=1`. Headline numbers are never sent |
| Gnani (voice) | Only when the user records audio or presses "read aloud" | The recording, or the answer text the user chose to hear | Anything the user did not ask to hear |
| Brevo (email) | Sign-up, login and email-change codes | The code and the account email | Any workspace data |

Retrieved conversation memory (Chroma, stored on the server) keeps only the
question and the interpreted request. Rows, SQL and verifier summaries are not
stored, because memory is sent back to the model as context.

Categorical hints read real values, so they are filtered: a value with a digit,
an email, or more than 40 characters is dropped, and tables imported from
financial reports (Tally) never get hints. `PUCHOO_SCHEMA_VALUE_HINTS=0` turns
hints off everywhere.

## Logs

Every log record is scrubbed when it is created, before any handler prints it:

- emails become `<email>`, numbers with 10 or more digits become `<number>`,
  and single-quoted text becomes `'?'`;
- exception messages are replaced with `<message redacted>` (the stack is
  kept), because pandas, SQLite and driver errors can quote a cell value;
- the query pipeline logs route, table names, counts and SQL with literals
  removed. It does not log the question, rows or repair feedback.

## Deleting an upload

Only the workspace owner can delete a data source (admins can still upload and
connect sources). That removes the stored SQLite file and its journal files, the report metadata
sidecar, the workspace row and its history (cascade), pending queries, its
notifications, cached translations, and schedules removal of its conversation
memory. The original uploaded file is never written to disk; it is parsed in
memory.

The owner of a business or school can also delete the whole organization from
Settings. That needs the exact organization name and the owner's current
password, is rate limited, and removes every account, session, invite, source
and its derived state, including cached translations. The only log line is
`organization_deleted` with the tenant id and the number of accounts removed.

## Real data and git

Never commit real exports. `.gitignore` blocks `*.numbers`, `data/private/`,
`tally_exports/`, `private_data/` and `real_data/`. Test fixtures are generated
from code with a fake company.
