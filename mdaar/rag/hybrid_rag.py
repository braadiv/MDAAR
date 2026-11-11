"""
Persistent Hybrid BM25 + Code-Aware RAG Engine for MDAAR v2.0.0.
Indexes generated documentation chunks alongside deterministic Codebase Fact Cards,
Database Tables, API Endpoints, and Module Summaries, with Arabic normalization,
camelCase/snake_case code tokenization, audience authorization views
(Developer / Management / End-User), and disk persistence in project_dir/.rag_index/.
"""

from collections import Counter
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re

from mdaar.security.sanitizer import SecretSanitizer
from mdaar.skills.loader import SkillLoader


ARABIC_DIACRITICS_RE = re.compile(r"[\u064B-\u065F\u0670\u0640]")
NON_WORD_RE = re.compile(r"[^\w\u0600-\u06FF]+")
CAMEL_SPLIT_RE = re.compile(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")

AUDIENCE_ALLOWED_PREFIXES = {
    "End-User": ("04_User-Guides/", "05_Release-Documentation/", "index.md", "01_Administrative/Scope.md"),
    "Management": (
        "00_Governance/",
        "01_Administrative/",
        "02_Technical/SRS.md",
        "02_Technical/SAD.md",
        "02_Technical/HLD.md",
        "05_Release-Documentation/",
        "index.md",
    ),
    "Developer": None,  # Full access to docs + codebase facts + schemas + APIs
}


@dataclass
class IndexedChunk:
    chunk_id: str
    source: str
    section: str
    kind: str  # "doc", "code_fact", "schema", "api", "module_summary"
    content: str
    token_count: int = 0


def normalize_arabic(text):
    """Normalize Arabic character variants and strip diacritics/tatweel."""
    text = ARABIC_DIACRITICS_RE.sub("", text)
    text = text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    text = text.replace("ى", "ي").replace("ة", "ه")
    return text


def tokenize_bilingual(text):
    """Tokenize mixed Arabic + English code identifiers (camelCase, snake_case, kebab-case, paths)."""
    if not text:
        return []
    raw_words = NON_WORD_RE.split(text)
    tokens = []
    for word in raw_words:
        if not word:
            continue
        # Preserve original lowercase form
        norm_word = normalize_arabic(word.lower())
        if len(norm_word) > 1:
            tokens.append(norm_word)

        # Strip common Arabic definite article/conjunction prefixes as auxiliary search tokens
        if any("\u0600" <= ch <= "\u06FF" for ch in norm_word):
            if norm_word.startswith("ال") and len(norm_word) > 4:
                tokens.append(norm_word[2:])
            elif len(norm_word) > 4 and norm_word[0] in ("و", "ب", "ل", "ك", "ف") and norm_word[1:3] == "ال":
                tokens.append(norm_word[3:])

        # Split camelCase & snake_case identifier parts for high code recall
        split_camel = CAMEL_SPLIT_RE.sub(" ", word)
        for part in re.split(r"[\s_]+", split_camel):
            p = part.lower()
            if len(p) > 1 and p != norm_word:
                tokens.append(p)
    return tokens


class DocumentationChatEngine:
    """Persistent Hybrid BM25 Q&A Engine stored in project_dir/.rag_index/."""

    def __init__(self, docs_src_dir, gateway, index_dir=None, skill_loader=None):
        self.docs_src_dir = Path(docs_src_dir)
        self.gateway = gateway
        self.skill_loader = skill_loader or SkillLoader()
        if index_dir is not None:
            self.index_dir = Path(index_dir)
        else:
            # Default: project_dir/.rag_index when docs_src_dir is project_dir/docs_repo/docs
            self.index_dir = self.docs_src_dir.parent.parent / ".rag_index"
        self.index_file = self.index_dir / "index.json"

        self.chunks = []
        self._doc_term_freqs = []
        self._idf = {}
        self._avg_dl = 1.0
        self.documents = {}  # Backwards compatibility view {rel_path: full_text}

    def load_documents(self, prefer_saved_index=True):
        """Load persisted index from .rag_index/index.json if present, or index docs_src_dir."""
        if prefer_saved_index and self.load_index():
            return len(self.chunks)
        return self.build_index()

    def build_index(self, fact_sheet=None, tables=None, endpoints=None, module_findings=None, save=True):
        """Index all Markdown docs plus verified codebase facts, DB tables, APIs, and module summaries."""
        chunks = []
        self.documents = {}

        # 1. Index generated Markdown docs split cleanly by headings
        if self.docs_src_dir.exists():
            for md_file in sorted(self.docs_src_dir.rglob("*.md")):
                rel = md_file.relative_to(self.docs_src_dir).as_posix()
                text = md_file.read_text(encoding="utf-8", errors="replace")
                clean_text = SecretSanitizer.sanitize(text, rel)
                self.documents[rel] = clean_text
                for idx, (heading, body) in enumerate(_split_markdown_sections(clean_text, rel), 1):
                    if len(body.strip()) < 25:
                        continue
                    chunks.append(
                        IndexedChunk(
                            chunk_id=f"doc:{rel}#{idx}",
                            source=rel,
                            section=heading,
                            kind="doc",
                            content=body.strip(),
                        )
                    )

        # 2. Index deterministic FileFactCards (ground truth even for small doc templates)
        if fact_sheet is not None:
            summary_body = fact_sheet.to_prompt_context(max_cards=60)
            chunks.append(
                IndexedChunk(
                    chunk_id="fact:project_overview",
                    source="CODEBASE_FACT_SHEET",
                    section=f"Project Fact Sheet ({fact_sheet.project_name})",
                    kind="code_fact",
                    content=summary_body,
                )
            )
            # Group FileFactCards in batches of 15 for high-signal BM25 lookup
            cards = fact_sheet.file_cards
            for i in range(0, len(cards), 15):
                batch = cards[i : i + 15]
                body = "\n".join(c.to_compact_line() for c in batch)
                chunks.append(
                    IndexedChunk(
                        chunk_id=f"fact:cards#{i // 15 + 1}",
                        source="CODEBASE_FILE_CARDS",
                        section=f"Files {i + 1}-{i + len(batch)}",
                        kind="code_fact",
                        content=body,
                    )
                )

        # 3. Index Database Tables
        if tables:
            for t in tables:
                cols_str = ", ".join(
                    f"{c.name}:{c.data_type}{'(PK)' if c.is_pk else ''}{'(FK)' if c.is_fk else ''}"
                    for c in t.columns
                )
                chunks.append(
                    IndexedChunk(
                        chunk_id=f"schema:{t.name}",
                        source=t.source_file or "DATABASE_SCHEMA",
                        section=f"Table `{t.name}`",
                        kind="schema",
                        content=f"Table `{t.name}` (source: `{t.source_file}`)\nColumns: {cols_str}",
                    )
                )

        # 4. Index API Endpoints
        if endpoints:
            for i in range(0, len(endpoints), 20):
                batch = endpoints[i : i + 20]
                lines = [
                    f"- `{ep.method} {ep.path}` | handler={ep.handler} | auth={ep.auth_hint or 'TBD'} | source=`{ep.source_file}`"
                    for ep in batch
                ]
                chunks.append(
                    IndexedChunk(
                        chunk_id=f"api:batch#{i // 20 + 1}",
                        source="API_CATALOG",
                        section=f"API Endpoints {i + 1}-{i + len(batch)}",
                        kind="api",
                        content="\n".join(lines),
                    )
                )

        # 5. Index Module Map Findings
        if module_findings:
            for mod, notes in sorted(module_findings.items()):
                joined = "\n\n".join(notes)
                for part_idx in range(0, len(joined), 2000):
                    snippet = joined[part_idx : part_idx + 2000]
                    chunks.append(
                        IndexedChunk(
                            chunk_id=f"map:{mod}#{part_idx // 2000 + 1}",
                            source=f"module:{mod}",
                            section=f"Module Deep Analysis: {mod}",
                            kind="module_summary",
                            content=snippet,
                        )
                    )

        self._compile_bm25(chunks)
        if save:
            self.save_index()
        return len(self.chunks)

    def _compile_bm25(self, chunks):
        self.chunks = chunks
        self._doc_term_freqs = []
        df = Counter()
        total_len = 0

        for ch in self.chunks:
            text_for_index = f"{ch.source} {ch.section}\n{ch.content}"
            toks = tokenize_bilingual(text_for_index)
            ch.token_count = len(toks)
            total_len += len(toks)
            tf = Counter(toks)
            self._doc_term_freqs.append(tf)
            for term in tf.keys():
                df[term] += 1

        n_docs = max(1, len(self.chunks))
        self._avg_dl = (total_len / n_docs) if n_docs else 1.0
        self._idf = {
            term: math.log((n_docs - freq + 0.5) / (freq + 0.5) + 1.0)
            for term, freq in df.items()
        }

    def save_index(self):
        """Persist chunks and BM25 metadata to project_dir/.rag_index/index.json."""
        self.index_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": "2.0.0",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "chunk_count": len(self.chunks),
            "chunks": [asdict(c) for c in self.chunks],
        }
        self.index_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return self.index_file

    def load_index(self):
        """Load persisted index from project_dir/.rag_index/index.json if available."""
        if not self.index_file.is_file():
            return False
        try:
            payload = json.loads(self.index_file.read_text(encoding="utf-8"))
            raw_chunks = payload.get("chunks", [])
            chunks = [IndexedChunk(**c) for c in raw_chunks]
            self._compile_bm25(chunks)
            # Populate self.documents view for doc chunks
            for ch in chunks:
                if ch.kind == "doc":
                    self.documents.setdefault(ch.source, "")
                    self.documents[ch.source] += ("\n\n" if self.documents[ch.source] else "") + ch.content
            return True
        except Exception:
            return False

    def _is_allowed_for_audience(self, chunk, audience):
        allowed = AUDIENCE_ALLOWED_PREFIXES.get(audience)
        if allowed is None:
            return True
        if chunk.kind != "doc":
            return False
        return any(chunk.source.startswith(prefix) or chunk.source == prefix for prefix in allowed)

    def search_relevant(self, query, top_k=6, audience="Developer"):
        """Return top-K relevant (source_label, content) snippets using Hybrid BM25 + audience view."""
        if not self.chunks:
            self.load_documents()
        if not self.chunks:
            return []

        q_tokens = tokenize_bilingual(query)
        k1 = 1.5
        b = 0.75
        q_norm = normalize_arabic(query.lower())

        scored = []
        for idx, ch in enumerate(self.chunks):
            if not self._is_allowed_for_audience(ch, audience):
                continue

            tf = self._doc_term_freqs[idx]
            dl = max(1, ch.token_count)
            score = 0.0
            for qt in q_tokens:
                if qt not in tf:
                    continue
                f = tf[qt]
                idf = self._idf.get(qt, 0.0)
                score += idf * ((f * (k1 + 1.0)) / (f + k1 * (1.0 - b + b * (dl / max(1.0, self._avg_dl)))))

            # Slight metadata boost if query hits heading/source directly
            meta_norm = normalize_arabic(f"{ch.source} {ch.section}".lower())
            if q_norm and any(t in meta_norm for t in q_tokens if len(t) > 2):
                score += 1.25

            # Boost technical architecture/fact chunks slightly on architectural queries
            if audience == "Developer" and ch.kind in ("code_fact", "schema", "api"):
                score += 0.15

            if score > 0:
                scored.append((score, ch))

        # Fallback to End-User/Management unfiltered doc search ONLY if strict filter yielded 0 hits
        if not scored and audience != "Developer":
            return self.search_relevant(query, top_k=top_k, audience="Developer")

        scored.sort(key=lambda x: x[0], reverse=True)
        if not scored:
            # Return top project overview/doc chunks as baseline context rather than empty
            baseline = [c for c in self.chunks if self._is_allowed_for_audience(c, audience)][:top_k]
            return [(f"{c.source} ({c.section})", c.content[:1800]) for c in baseline]

        return [
            (f"{ch.source} :: {ch.section}", ch.content[:2000])
            for _, ch in scored[:top_k]
        ]

    def ask(self, question, audience="Developer"):
        """Answer a natural-language question grounded strictly in the indexed docs and codebase facts."""
        if not self.chunks:
            self.load_documents()

        snippets = self.search_relevant(question, top_k=6, audience=audience)
        if not snippets:
            return "⚠️ لا توجد وثائق أو بيانات كود مفهرسة حالياً. يرجى تشغيل مرحلة البناء والتوليد أولاً."

        context_str = "\n\n---\n\n".join(
            f"[المصدر: {src}]\n{txt}" for src, txt in snippets
        )
        skills_text = self.skill_loader.load_for_role("rag")
        sys_prompt = (
            "أنت مساعد التوثيق والشفرة البرمجية لمنظومة MDAAR v2.0.\n"
            f"الجمهور المستهدف حالياً: `{audience}`.\n"
            "التزم حرفياً بالقواعد والمهارات التالية:\n"
            f"{skills_text}"
        )
        prompt = (
            f"سؤال المستخدم: {question}\n\n"
            f"المقاطع المسترجعة من الفهرس ({len(snippets)} مقاطع):\n{context_str}\n\n"
            "أجب بدقة ووضوح مستنداً فقط إلى المقاطع المسترجعة أعلاه، واذكر المصادر الفعلية."
        )

        if hasattr(self.gateway, "generate_as_executor"):
            return self.gateway.generate_as_executor(prompt, sys_prompt)
        return self.gateway.generate(prompt, sys_prompt)


def _split_markdown_sections(markdown_text, rel_path, max_chunk_chars=1800):
    """Split Markdown by ## / ### headings, subdividing oversized sections cleanly."""
    lines = markdown_text.splitlines()
    sections = []
    current_heading = Path(rel_path).stem
    current_buf = []

    for line in lines:
        if re.match(r"^#{1,3}\s+", line) and current_buf:
            sections.append((current_heading, "\n".join(current_buf)))
            current_heading = line.lstrip("#").strip()
            current_buf = [line]
        else:
            if re.match(r"^#{1,3}\s+", line) and not current_buf:
                current_heading = line.lstrip("#").strip()
            current_buf.append(line)

    if current_buf:
        sections.append((current_heading, "\n".join(current_buf)))

    # Break any giant table/section cleanly so retrieval stays pinpointed
    bounded = []
    for heading, body in sections:
        if len(body) <= max_chunk_chars:
            bounded.append((heading, body))
        else:
            for i in range(0, len(body), max_chunk_chars):
                bounded.append((f"{heading} (جزء {i // max_chunk_chars + 1})", body[i : i + max_chunk_chars]))
    return bounded
