
import json
import requests
from typing import Iterator

OLLAMA_BASE  = "http://localhost:11434"
DEFAULT_MODEL = "llama3"


class OllamaClient:

    def __init__(self, model: str = DEFAULT_MODEL, base_url: str = OLLAMA_BASE):
        self.model    = model
        self.base_url = base_url

    def is_running(self) -> bool:
        try:
            r = requests.get(f"{self.base_url}/api/tags", timeout=3)
            return r.status_code == 200
        except Exception:
            return False

    def list_models(self) -> list:
        try:
            r = requests.get(f"{self.base_url}/api/tags", timeout=5)
            return [m["name"] for m in r.json().get("models", [])]
        except Exception:
            return []


    def stream(self, prompt: str, system: str = "", temperature: float = 0.2) -> Iterator[str]:
        """
        Stream tokens from Ollama one chunk at a time.
        Yields each text token as it arrives.
        """
        payload = {
            "model":   self.model,
            "prompt":  prompt,
            "system":  system,
            "stream":  True,
            "options": {"temperature": temperature},
        }

        with requests.post(f"{self.base_url}/api/generate", json=payload, stream=True, timeout=120) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if line:
                    obj = json.loads(line)
                    if token := obj.get("response"):
                        yield token
                    if obj.get("done"):
                        break

    def generate(self, prompt: str, system: str = "", temperature: float = 0.2) -> str:
        """Blocking generate — returns full response string."""
        return "".join(self.stream(prompt, system, temperature))



    def chat_stream(self, messages: list, system: str = "", temperature: float = 0.3) -> Iterator[str]:
        """
        Multi-turn chat with streaming.
        messages = [{"role": "user"|"assistant", "content": "..."}]
        """
        payload = {
            "model":    self.model,
            "messages": [{"role": "system", "content": system}] + messages if system else messages,
            "stream":   True,
            "options":  {"temperature": temperature},
        }

        with requests.post(f"{self.base_url}/api/chat", json=payload, stream=True, timeout=120) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if line:
                    obj = json.loads(line)
                    if token := obj.get("message", {}).get("content"):
                        yield token
                    if obj.get("done"):
                        break

    def chat(self, messages: list, system: str = "", temperature: float = 0.3) -> str:
        return "".join(self.chat_stream(messages, system, temperature))

    def generate_json(self, prompt: str, system: str = "", retries: int = 2) -> dict:
        """
        Ask Ollama to return JSON and parse it.
        Retries on malformed JSON up to `retries` times.
        """
        json_system = system + "\n\nIMPORTANT: Respond ONLY with valid JSON. No text before or after. No markdown code fences."

        for attempt in range(retries + 1):
            raw = self.generate(prompt, json_system, temperature=0.1)
            # Strip markdown fences if model ignored instructions
            clean = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
            try:
                return json.loads(clean)
            except json.JSONDecodeError:
                # Try to extract JSON object from messy output
                import re
                match = re.search(r'\{[\s\S]*\}', clean)
                if match:
                    try:
                        return json.loads(match.group())
                    except Exception:
                        pass
                if attempt == retries:
                    return {"error": "Could not parse JSON", "raw": raw[:500]}

        return {}
