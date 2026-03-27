import re
import json
import hashlib
import chromadb
from datetime import datetime
from dataclasses import dataclass, field, asdict
from sentence_transformers import SentenceTransformer


@dataclass
class LogChunk:
    chunk_id:      str
    pipeline_id:   str
    pipeline_type: str   # github-actions | jenkins | gitlab | terraform | docker
    stage:         str   # build | test | deploy | infra | unknown
    content:       str
    error_lines:   list
    severity:      str   # critical | high | medium | low | info
    timestamp:     str
    metadata:      dict = field(default_factory=dict)


@dataclass
class FailureRecord:
    failure_id:    str
    pipeline_type: str
    error_type:    str
    root_cause:    str
    log_summary:   str
    fix_applied:   str
    fix_commands:  list
    resolved_at:   str
    tags:          list = field(default_factory=list)


class LogChunker:
    """
    Splits raw CI/CD logs into meaningful segments by pipeline stage.
    Extracts error contexts with surrounding lines for better RAG recall.
    """

    STAGE_PATTERNS = {
        "github-actions": [r"##\[group\](.+)", r"Run (.+)"],
        "jenkins":        [r"\[Pipeline\] stage \((.+)\)", r"Running stage: (.+)"],
        "gitlab":         [r'Executing "(.+)" stage', r"Running with gitlab-runner"],
        "terraform":      [r"Running: terraform (.+)", r"Terraform (.+)\.\.\."],
        "docker":         [r"Step \d+/\d+ : (.+)", r"RUN (.+)"],
    }

    ERROR_PATTERNS = [
        r"(?i)\b(error|err|fatal|failed|failure|exception|traceback|panic)\b",
        r"(?i)(exit code [^0]|non-zero exit|returned \d+[^0])",
        r"(?i)(cannot|could not|unable to|not found|does not exist)",
        r"(?i)(permission denied|access denied|unauthorized)",
        r"(?i)(timeout|timed out|connection refused|ECONNREFUSED)",
        r"(?i)(oom killed|out of memory|killed|OOMKilled)",
        r"(?i)(ERESOLVE|ENOENT|EACCES|EPERM|MODULE_NOT_FOUND)",
    ]

    def chunk(self, raw_log: str, pipeline_id: str, pipeline_type: str) -> list:
        lines = raw_log.strip().splitlines()
        stages = self._split_stages(lines, pipeline_type)
        chunks = []

        for stage_name, stage_lines in stages.items():
            error_lines = self._extract_errors(stage_lines)
            severity    = self._severity(stage_lines)
            content     = "\n".join(stage_lines)
            chunk_id    = hashlib.md5(f"{pipeline_id}:{stage_name}:{content[:80]}".encode()).hexdigest()[:12]

            chunks.append(LogChunk(
                chunk_id      = chunk_id,
                pipeline_id   = pipeline_id,
                pipeline_type = pipeline_type,
                stage         = stage_name,
                content       = content,
                error_lines   = error_lines,
                severity      = severity,
                timestamp     = datetime.utcnow().isoformat(),
                metadata      = {"line_count": len(stage_lines), "error_count": len(error_lines)}
            ))

        chunks.sort(key=lambda c: {"critical":0,"high":1,"medium":2,"low":3,"info":4}.get(c.severity, 5))
        return chunks

    def _split_stages(self, lines: list, pipeline_type: str) -> dict:
        patterns = self.STAGE_PATTERNS.get(pipeline_type, [])
        stages, current, current_lines = {}, "init", []

        for line in lines:
            matched = False
            for pat in patterns:
                m = re.search(pat, line)
                if m:
                    if current_lines:
                        stages[current] = current_lines
                    current = (m.group(1) if m.lastindex else f"stage_{len(stages)}").strip()[:60]
                    current_lines = [line]
                    matched = True
                    break
            if not matched:
                current_lines.append(line)

        if current_lines:
            stages[current] = current_lines

        return stages or {"full_log": lines}

    def _extract_errors(self, lines: list, ctx: int = 6) -> list:
        hits = set()
        for i, line in enumerate(lines):
            for pat in self.ERROR_PATTERNS:
                if re.search(pat, line):
                    hits.update(range(max(0, i - ctx), min(len(lines), i + ctx + 1)))
                    break
        return [lines[i] for i in sorted(hits)]

    def _severity(self, lines: list) -> str:
        text = "\n".join(lines).lower()
        if any(k in text for k in ["fatal","panic","oom killed","out of memory","segfault"]): return "critical"
        if any(k in text for k in ["error","failed","failure","exception","non-zero"]):        return "high"
        if any(k in text for k in ["warning","warn","deprecated"]):                            return "medium"
        return "info"


class EmbeddingEngine:
    """
    Local embeddings via sentence-transformers.
    Model is downloaded once and cached in ~/.cache/huggingface/
    No API key, no internet call after first download.
    """

    MODEL_NAME = "all-MiniLM-L6-v2"   # 80MB, fast, good quality

    def __init__(self):
        print(f"  Loading embedding model [{self.MODEL_NAME}]...")
        self.model = SentenceTransformer(self.MODEL_NAME)
        print("  Embedding model ready.")

    def embed(self, text: str) -> list:
        return self.model.encode(text, normalize_embeddings=True).tolist()

    def embed_batch(self, texts: list) -> list:
        return self.model.encode(texts, normalize_embeddings=True).tolist()


# ── Vector Store ──────────────────────────────────────────────────────────────

class VectorStore:
    """
    Persistent ChromaDB vector store saved to ./chroma_db/
    Survives restarts — PipelineIQ remembers failures across sessions.
    """

    COLLECTION = "pipeline_failures"

    def __init__(self, persist_dir: str = "./chroma_db"):
        self.client     = chromadb.PersistentClient(path=persist_dir)
        self.collection = self.client.get_or_create_collection(
            name      = self.COLLECTION,
            metadata  = {"hnsw:space": "cosine"}
        )

    def add(self, doc_id: str, text: str, embedding: list, metadata: dict):
        # ChromaDB upserts by ID
        self.collection.upsert(
            ids        = [doc_id],
            embeddings = [embedding],
            documents  = [text],
            metadatas  = [metadata]
        )

    def search(self, embedding: list, top_k: int = 4, where: dict = None) -> list:
        kwargs = dict(query_embeddings=[embedding], n_results=min(top_k, max(self.collection.count(), 1)))
        if where:
            kwargs["where"] = where
        results = self.collection.query(**kwargs)

        out = []
        for i in range(len(results["ids"][0])):
            out.append({
                "id":       results["ids"][0][i],
                "text":     results["documents"][0][i],
                "metadata": results["metadatas"][0][i],
                "score":    round(1 - results["distances"][0][i], 3),
            })
        return out

    def count(self) -> int:
        return self.collection.count()


class RAGPipeline:
    """
    Ties chunker + embedder + vector store together.

    Usage:
        rag = RAGPipeline()
        rag.store_failure(log_text, "github-actions", resolution_dict)
        similar = rag.retrieve_similar(new_log, "github-actions", top_k=3)
    """

    def __init__(self, persist_dir: str = "./chroma_db"):
        self.chunker   = LogChunker()
        self.embedder  = EmbeddingEngine()
        self.store     = VectorStore(persist_dir)
        self._seed_knowledge_base()

    def store_failure(self, log_text: str, pipeline_type: str, resolution: dict) -> str:
        """
        Store a resolved failure so future queries benefit from it.
        Call this after every fix. The system improves over time.
        """
        pipeline_id = hashlib.md5(log_text[:200].encode()).hexdigest()[:12]
        chunks      = self.chunker.chunk(log_text, pipeline_id, pipeline_type)
        hot_chunks  = [c for c in chunks if c.severity in ("critical","high")] or chunks[:2]

        for chunk in hot_chunks:
            embed_text = (
                f"pipeline:{pipeline_type} stage:{chunk.stage} "
                f"errors:{' '.join(chunk.error_lines[:8])} "
                f"fix:{resolution.get('fix_applied','')} "
                f"type:{resolution.get('error_type','')}"
            )
            embedding = self.embedder.embed(embed_text)
            self.store.add(
                doc_id    = chunk.chunk_id,
                text      = embed_text,
                embedding = embedding,
                metadata  = {
                    "pipeline_type": pipeline_type,
                    "stage":         chunk.stage,
                    "severity":      chunk.severity,
                    "error_type":    resolution.get("error_type", ""),
                    "root_cause":    resolution.get("root_cause", ""),
                    "fix_applied":   resolution.get("fix_applied", ""),
                    "fix_commands":  json.dumps(resolution.get("fix_commands", [])),
                    "tags":          json.dumps(resolution.get("tags", [])),
                }
            )

        return pipeline_id

    def retrieve_similar(self, log_text: str, pipeline_type: str, top_k: int = 3) -> list:
        """Return top-k past failures most similar to the current log."""
        if self.store.count() == 0:
            return []

        query_embed = self.embedder.embed(log_text[:1000])
        results     = self.store.search(query_embed, top_k=top_k)

        similar = []
        for r in results:
            if r["score"] > 0.25:
                similar.append({
                    "score":         r["score"],
                    "error_type":    r["metadata"].get("error_type"),
                    "root_cause":    r["metadata"].get("root_cause"),
                    "fix_applied":   r["metadata"].get("fix_applied"),
                    "fix_commands":  json.loads(r["metadata"].get("fix_commands", "[]")),
                    "stage":         r["metadata"].get("stage"),
                })
        return similar

    def _seed_knowledge_base(self):
        """Pre-seed with common DevOps failure patterns (only if DB is empty)."""
        if self.store.count() > 0:
            return

        seeds = [
            ("npm error ERESOLVE unable to resolve dependency tree\npeer react@\"^17.0.0\" from @legacy-component",
             "github-actions",
             {"error_type":"DependencyConflict","root_cause":"NPM peer dependency conflict between React versions",
              "fix_applied":"Add --legacy-peer-deps to npm ci","fix_commands":["npm ci --legacy-peer-deps"],"tags":["npm","peer-deps"]}),

            ("Error acquiring the state lock\nConditionalCheckFailedException",
             "terraform",
             {"error_type":"StateLock","root_cause":"Terraform state lock not released from previous run",
              "fix_applied":"Force unlock with lock ID from error","fix_commands":["terraform force-unlock <LOCK_ID>"],"tags":["terraform","state-lock"]}),

            ("Module not found: Error: Can't resolve '@/utils/analytics'\nwebpack compiled with 1 error",
             "docker",
             {"error_type":"MissingModule","root_cause":"Webpack module alias not configured in tsconfig/webpack",
              "fix_applied":"Add path alias to tsconfig.json","fix_commands":['# tsconfig.json: "paths": {"@/*": ["./src/*"]}'],"tags":["webpack","typescript"]}),

            ("signal: killed\nout of memory\nOOMKilled",
             "github-actions",
             {"error_type":"OOMKilled","root_cause":"Runner exceeded memory limit during test execution",
              "fix_applied":"Increase runner memory or split test jobs","fix_commands":["NODE_OPTIONS=--max-old-space-size=4096 npm test"],"tags":["oom","memory"]}),

            ("Permission denied (publickey)\nfatal: Could not read from remote repository",
             "gitlab",
             {"error_type":"AuthFailure","root_cause":"SSH key not configured or expired for git remote",
              "fix_applied":"Re-add deploy key to repository settings","fix_commands":["ssh-keyscan github.com >> ~/.ssh/known_hosts"],"tags":["ssh","auth","git"]}),
        ]

        for log, ptype, res in seeds:
            self.store_failure(log, ptype, res)
