# Reference: original open-WebUI artifacts

These files are the **original open-WebUI configuration** this backend was
derived from. They are kept for provenance and are **not** imported or executed
by the running service.

| File | Original role |
|---|---|
| `filter/language_extract.py` | open-WebUI **Filter** — its `inlet()` lifted a `language` field off the request and injected `[Language Code: xx]` into the last user message. Reproduced by `build_user_message()` in `main.py`. |
| `tools/database_query.py` | open-WebUI **Tool** class exposing the 4 retrieval functions, with `__event_emitter__` status updates and a `Valves` config block. Refactored into `rag_tools.py` (+ `db.py`, `inference.py`, `config.py`). |

See [../docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md) §7 for the full mapping
from open-WebUI concepts to this codebase.
