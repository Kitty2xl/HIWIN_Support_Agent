from pydantic import BaseModel
from typing import Optional


class Filter:
    class Valves(BaseModel):
        pass

    def __init__(self):
        self.valves = self.Valves()

    async def inlet(self, body: dict, user: Optional[dict] = None) -> dict:
        language_code = body.get("language")

        if language_code:
            messages = body.get("messages", [])

            # 2. Check if messages exist and the last one is from the user
            if messages and messages[-1].get("role") == "user":
                original_content = messages[-1].get("content", "")

                # 3. Inject the argument cleanly into the user's prompt
                messages[-1][
                    "content"
                ] = f"[Language Code: {language_code}]\n{original_content}"

            body.pop("language", None)

        return body
