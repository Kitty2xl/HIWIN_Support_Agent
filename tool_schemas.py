"""OpenAI function-calling schemas for the four RAG tools, plus a dispatch map.

The descriptions mirror the docstrings the open-WebUI tool exposed so the model
routes between them the same way (manuals vs. URLs vs. certifications).
"""

import rag_tools

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "db_search_technical_manuals",
            "description": (
                "Returns a list of technical/product manual passages relevant to the "
                "subject queried. DOES NOT contain download links, download information, "
                "or download URLs. Use for general product information and specifications."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query. Include product series/model names here (e.g. 'EG series linear guideway load specifications').",
                    },
                    "language_code": {
                        "type": "string",
                        "description": "Language code to filter results (e.g. 'en', 'tc', 'jp'). Must match the language_code stored in the document metadata.",
                    },
                    "product_table": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Full table name(s) as returned by db_get_available_product_tables (e.g. ['data_linear_guideway']). Do NOT pass a product series or model name.",
                    },
                },
                "required": ["query", "language_code", "product_table"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "db_get_available_product_tables",
            "description": "Returns a list of all available product tables currently in the database. Call this first to learn valid product_table values.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "db_search_certifications",
            "description": "Returns a list of certificates and their respective image file paths. Use for certifications, compliance documents, or proof of conformance.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The certification search query."},
                    "language_code": {
                        "type": "string",
                        "description": "Language code to filter results (e.g. 'en', 'tc', 'jp').",
                    },
                },
                "required": ["query", "language_code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "db_search_product_urls",
            "description": "Returns product information links/URLs and CAD download links/URLs. ONLY CALL ONCE — it returns all records and is sufficient to answer any link/download query.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

# Tool name -> async callable.
DISPATCH = {
    "db_search_technical_manuals": rag_tools.db_search_technical_manuals,
    "db_get_available_product_tables": rag_tools.db_get_available_product_tables,
    "db_search_certifications": rag_tools.db_search_certifications,
    "db_search_product_urls": rag_tools.db_search_product_urls,
}
