"""
analyzer.py
───────────
Wires RAG retrieval + Ollama LLM into one cohesive analyzer.
This is the brain — call analyze() for any CI/CD failure log.
"""

import json
from rag_engine  import RAGPipeline, LogChunker
from llm_client  import OllamaClient

ANALYSIS_SYSTEM = """You are PipelineIQ — a senior DevOps engineer AI with deep expertise in CI/CD systems.

Your job: analyze pipeline failure logs and return a precise root cause analysis as JSON.

Rules:
- Be SPECIFIC. Name the exact error, exit code, or misconfiguration.
- Give ACTIONABLE fixes with real commands, not vague advice.
- Use the similar past failures provided to improve accuracy.
- Respond ONLY with valid JSON — no text outside the braces.

JSON schema (follow exactly):
{
  "root_cause": "One specific sentence naming the exact cause",
  "stage": "build | test | deploy | infra | unknown",
  "error_type": "DependencyConflict | StateLock | MissingModule | OOM | AuthFailure | NetworkTimeout | ConfigError | BuildError | TestFailure | Other",
  "explanation": "2-3 sentences explaining WHY this happened",
  "fix": {
    "summary": "One line describing the fix",
    "steps": ["step 1", "step 2", "step 3"],
    "command": "exact shell command or empty string"
  },
  "confidence": "High | Medium | Low",
  "related_risks": ["risk 1", "risk 2"],
  "prevention_tip": "One specific tip to prevent recurrence"
}"""

CHAT_SYSTEM = """You are PipelineIQ — a senior DevOps AI assistant.
You help engineers understand and fix CI/CD pipeline failures.
Be concise and specific. Use backticks for commands and file paths.
Base your answers on the failure context provided."""


class PipelineAnalyzer:

    def __init__(self, model: str = "llama3"):
        print("[PipelineIQ] Initializing RAG engine...")
        self.rag     = RAGPipeline()
        self.llm     = OllamaClient(model=model)
        self.chunker = LogChunker()
        print(f"[PipelineIQ] Ready. Model: {model} | Knowledge base: {self.rag.store.count()} entries")



    def analyze(self, log_text: str, pipeline_type: str = "github-actions") -> dict:
        """
        Full RAG + LLM analysis pipeline.

        Steps:
          1. Chunk log by stage
          2. Retrieve similar past failures (RAG)
          3. Build context-rich prompt
          4. LLM generates structured root cause analysis
          5. Return parsed result
        """

        # 1. Chunk and extract error context
        chunks      = self.chunker.chunk(log_text, "query", pipeline_type)
        hot_chunks  = [c for c in chunks if c.severity in ("critical","high")] or chunks[:3]
        error_ctx   = "\n\n".join(
            f"[Stage: {c.stage} | Severity: {c.severity}]\n" + "\n".join(c.error_lines[:12])
            for c in hot_chunks
        )

        # 2. RAG retrieval
        similar = self.rag.retrieve_similar(log_text, pipeline_type, top_k=3)

        # 3. Build prompt
        prompt = self._build_prompt(error_ctx, pipeline_type, similar)

        # 4. LLM call
        result = self.llm.generate_json(prompt, ANALYSIS_SYSTEM)

        # 5. Attach metadata
        result["similar_failures"]  = similar
        result["pipeline_type"]     = pipeline_type
        result["chunks_analyzed"]   = len(chunks)

        return result

    def analyze_stream(self, log_text: str, pipeline_type: str = "github-actions"):
        """
        Streaming version — yields tokens as they arrive.
        Used by the CLI for live output.
        """
        chunks     = self.chunker.chunk(log_text, "query", pipeline_type)
        hot_chunks = [c for c in chunks if c.severity in ("critical","high")] or chunks[:3]
        error_ctx  = "\n\n".join(
            f"[Stage: {c.stage}]\n" + "\n".join(c.error_lines[:12])
            for c in hot_chunks
        )
        similar = self.rag.retrieve_similar(log_text, pipeline_type, top_k=3)
        prompt  = self._build_prompt(error_ctx, pipeline_type, similar)

        yield from self.llm.stream(prompt, ANALYSIS_SYSTEM, temperature=0.1)



    def chat(self, message: str, history: list, log_context: str = "", pipeline_type: str = "") -> Iterator[str]:
        """
        Follow-up Q&A chat with streaming.
        history = [{"role":"user"|"assistant","content":"..."}]
        """
        system = CHAT_SYSTEM
        if log_context:
            system += f"\n\nCurrent failure context ({pipeline_type}):\n{log_context[:800]}"

        messages = history + [{"role": "user", "content": message}]
        yield from self.llm.chat_stream(messages, system)



    def record_fix(self, log_text: str, pipeline_type: str, resolution: dict) -> str:
        """
        Teach PipelineIQ a new resolution.
        Call after every fix — this is how the system gets smarter over time.

        resolution = {
            "root_cause":    "...",
            "error_type":    "DependencyConflict | ...",
            "fix_applied":   "...",
            "fix_commands":  ["cmd1", "cmd2"],
            "tags":          ["npm", "peer-deps"]
        }
        """
        fid = self.rag.store_failure(log_text, pipeline_type, resolution)
        print(f"[PipelineIQ] Recorded fix (id:{fid}). Knowledge base: {self.rag.store.count()} entries.")
        return fid

    def stats(self) -> dict:
        return {
            "knowledge_base_entries": self.rag.store.count(),
            "llm_model":              self.llm.model,
            "ollama_running":         self.llm.is_running(),
            "available_models":       self.llm.list_models(),
        }



    def _build_prompt(self, error_ctx: str, pipeline_type: str, similar: list) -> str:
        rag_block = ""
        if similar:
            rag_block = "\n\nSIMILAR PAST FAILURES (RAG context — use these to improve accuracy):\n"
            for i, f in enumerate(similar, 1):
                rag_block += (
                    f"\n  [{i}] ErrorType: {f.get('error_type')}  Similarity: {f.get('score')}\n"
                    f"       RootCause: {f.get('root_cause')}\n"
                    f"       Fix: {f.get('fix_applied')}\n"
                    f"       Commands: {', '.join(f.get('fix_commands', [])[:2])}\n"
                )

        return (
            f"Analyze this {pipeline_type} pipeline failure.\n\n"
            f"--- ERROR CONTEXT ---\n{error_ctx}\n--- END ---"
            f"{rag_block}\n\n"
            f"Return JSON analysis only."
        )


# Allow importing Iterator without circular issues
from typing import Iterator
PipelineAnalyzer.chat.__annotations__["return"] = Iterator[str]
