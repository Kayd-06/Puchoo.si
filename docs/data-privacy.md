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
financial reports (Tally) never get hints. Once a company holds any Tally
report, none of its tables get hints. `PUCHOO_SCHEMA_VALUE_HINTS=0` turns
hints off everywhere.

Table names are part of the schema, so they are sent with every question. A
Tally report's table is named after the report type (for example
`sales_register`), not after the file or the company. When you name a table
yourself, keep company and party names out of it. The friendly file name you
see in Settings, the original file name and the report header stay on the
server.

When a company has many tables (more than `PUCHOO_SCHEMA_FOCUS_AFTER_TABLES`,
5 by default), only the tables that match the question are sent (at most
`PUCHOO_SCHEMA_FOCUS_MAX_TABLES`, 6 by default). If nothing matches, every
table is sent as before.

## Logs

Every log record is scrubbed when it is created, before any handler prints it:

- emails become `<email>`, numbers with 10 or more digits become `<number>`,
  and single-quoted text becomes `'?'`;
- exception messages are replaced with `<message redacted>` (the stack is
  kept), because pandas, SQLite and driver errors can quote a cell value;
- the query pipeline logs route, table names, counts and SQL with literals
  removed. It does not log the question, rows or repair feedback.

## Where uploaded data is stored

An upload is turned into a SQLite file on the server's disk. Render's free plan
wipes that disk on every restart or redeploy, so the app also keeps a
compressed (zlib) copy of that SQLite file in the app database (the
`workspace_source_blobs` table in Neon Postgres). The small metadata file next
to it (the list of files in a company, their labels and periods, and any Tally
report headers) is kept with it. The copy is refreshed every time a file is
added, replaced or removed, and when another source is moved into the company. When the file is missing on disk, the app
writes it back from this copy before answering. A copy can only be restored for
the same source and the same organization it was saved for. The copy is never
sent to any outside service, and its contents are never logged (only the
source id and byte counts).

Copies larger than 50 MiB after compression are not kept (change this with
`PUCHOO_SOURCE_BLOB_MAX_BYTES`). Such a source works until the next restart and
then shows "Needs re-upload". A source wiped before this copy existed also
shows "Needs re-upload"; uploading the file again keeps the same source, its
name, history and settings.

Database backups (`backend/backups.py`) include these copies, because they are
part of the database.

## Companies with many files

A company is one data source with one table per file. Adding a file to a
company, replacing one file or removing one file changes that company's SQLite
file in a temporary copy first; the live file is swapped only when the change
succeeded, so a failed upload leaves the company as it was. Removing or
replacing a file also clears pending queries and cached schema and translations
for that company. Only owners and admins can add, replace, remove or move
files; editors can see the files list.

"Move into company" copies every table of another uploaded source in the same
organization into this company, then deletes the old source the same way as
"Deleting an upload" below (stored file, sidecar, database copy, pending
queries, cached translations and its conversation memory). Its question
history is moved to the company. Database (server) connections cannot be moved.

## Deleting an upload

Only the workspace owner can delete a data source (admins can still upload and
connect sources). That removes the stored SQLite file and its journal files, the report metadata
sidecar, the copy of the file kept in the database, the workspace row and its
history (cascade), pending queries, its
notifications, cached translations, and schedules removal of its conversation
memory. The original uploaded file is never written to disk; it is parsed in
memory.

The owner of a business or school can also delete the whole organization from
Settings. That needs the exact organization name and the owner's current
password, is rate limited, and removes every account, session, invite, source
and its derived state, including the file copies kept in the database and
cached translations. The only log line is
`organization_deleted` with the tenant id and the number of accounts removed.

## Real data and git

Never commit real exports. `.gitignore` blocks `*.numbers`, `data/private/`,
`tally_exports/`, `private_data/` and `real_data/`. Test fixtures are generated
from code with a fake company.
