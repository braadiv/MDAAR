# 🚀 MDAAR (Multi-role Dynamic Architecture for Automated Repositories)

[![Open in Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/braadiv/MDAAR/blob/main/documentation_pipeline.ipynb)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)

An enterprise-grade, 100% free, privacy-first, AI-driven documentation framework.
Designed to run completely on **Google Colab** (preserving local hardware resources), persist configuration and state in **Google Drive**, and broadcast a live interactive documentation portal via **MkDocs Material** and **Cloudflare Tunnel**.

---

## 📖 جدول المحتويات / Table of Contents
- [🌟 الرؤية المعمارية للمنظومة](#-الرؤية-المعمارية-للمنظومة)
- [💡 نصيحة هامة: مستودع توثيق خاص موحد لكل مشاريعك](#-نصيحة-هامة-مستودع-توثيق-خاص-موحد-لكل-مشاريعك)
- [📦 تثبيت واستيراد المكتبة كحزمة بايثون](#-تثبيت-واستيراد-المكتبة-كحزمة-بايثون)
- [🚀 تشغيل الدفاتر التفاعلية في كولاب (عربي / English)](#-تشغيل-الدفاتر-التفاعلية-في-كولاب-عربي--english)
- [🗂️ هيكلة مجلدات المشروع في Google Drive](#️-هيكلة-مجلدات-المشروع-في-google-drive)
- [🧩 منظومة الوكلاء والمحللات المتخصصة](#-منظومة-الوكلاء-والمحللات-المتخصصة)
- [📄 الإطار المرجعي الشامل (plan.md)](#-الإطار-المرجعي-الشامل-planmd)
- [📜 الترخيص (License)](#-الترخيص-license)

---

## 🌟 الرؤية المعمارية للمنظومة (v2.0.0)

1. **قراءة 100% من ملفات المشروع بدون حدود للسياق:**
   - فحص شامل لجميع ملفات الكود غير المستثناة عبر نمط **Map-Reduce** (`RepoScanner` + `SemanticFileChunker` + `MapperAgent`) مع تجاهل تلقائي لمجلدات `vendor`, `node_modules`, `.git`, `.gitignore` وتعقيم الأسرار عبر `SecretSanitizer`.
2. **معمارية النموذج المزدوج الهجينة (Planner + Executor):**
   - **إعدادات مودل الخطة (`planner_gateway`):** يصمم الخطة الهندسية الشاملة ويراجع الجودة والاتساق (`QualityAgent`).
   - **إعدادات مودل التنفيذ (`executor_gateway`):** يتولى دائماً فحص 100% من الملفات (`Map Pass`) وكتابة جميع حزم التوثيق التفصيلية والإجابة عبر RAG (مع خيار تشغيل نموذج واحد لكليهما، وإدارة تفريغ الـ VRAM تلقائياً عند اختيار نموذجين محليين على Colab T4).
3. **مهارات مدمجة لمنع الهلوسة داخل المستودع:**
   - تتثبت حزمة المهارات تلقائياً مع المكتبة (`core-grounding-qa`, `reverse-engineering`, `architecture-mermaid`, `api-openapi-spec`, `docs-technical-writer`, `ops-and-user-guides`, `rag-qa-grounding`) وتفرض بروتوكول `[TBD]` و `[⚠️ BLIND SPOT]`.
4. **محللات حتمية متعددة اللغات والأطر (`Polyglot Extractors`):**
   - استخراج الحقائق من PHP (Yii2, Laravel, Symfony)، Python (AST)، JS/TS، Java/Kotlin، C#/.NET، Go، Ruby، Rust، Dart/Flutter.
   - استخلاص قواعد البيانات (`SchemaParser`) من ملفات SQL DDL وهجرات Yii2/Laravel/Prisma إلى مخططات `Mermaid ERD` و`Data-Dictionary.md`.
   - استخلاص نقاط الاتصال (`APISpecParser`) من `Postman Collections` و`OpenAPI/Swagger` ومسارات `Yii2 urlManager/ActiveController/actionXxx` وFastAPI/Django/Laravel/Express/Spring/Go.
5. **محرك Hybrid RAG دائم في `.rag_index/` + بوابة جودة آلية:**
   - يفهرس الوثائق وحقائق الكود الحقيقية والجداول والـ APIs مع تطبيع العربية وتخصيص الجمهور (`Developer`, `Management`, `End-User`).
   - تدقيق تلقائي لمخططات Mermaid والاتساق وتحديث `Document-Register.md` (`L0` ➔ `L1`) و`Quality-Audit-Report.md`.
6. **بوابة توثيق تفاعلية فورية (MkDocs Material + Cloudflare Tunnel):**
   - موقع تفاعلي يدعم اللغة العربية والوضع الليلي ومخططات Mermaid ومفاتيح SSH مزدوجة مستقلة (`id_ed25519_source` & `id_ed25519_docs`).

---

## 💡 نصيحة هامة: مستودع توثيق خاص موحد لكل مشاريعك

> [!TIP]
> **أفضل ممارسة موصى بها للمطورين والشركات:**
> يُنصح بإنشاء **مستودع خاص (Private Repository)** واحد على GitHub أو GitLab وتسميته مثلاً: `my-org-docs` أو `central-documentation`.
> 
> يقوم نظام **MDAAR** تلقائياً بإنشاء مجلد فرعي لكل مشروع داخل مستودع التوثيق الموحد:
> ```text
> central-documentation/
> └── projects/
>     ├── Project_A/       # توثيق المشروع الأول كاملاً
>     │   ├── mkdocs.yml
>     │   └── docs/
>     ├── Project_B/       # توثيق المشروع الثاني كاملاً
>     └── Project_C/
> ```
> هذا يتيح لك توثيق عشرات المشاريع في مكان مركزي واحد ومستقل تماماً عن كود المصدر!

---

## 📦 تثبيت واستيراد المكتبة كحزمة بايثون

يمكنك تثبيت واستخدام محرك MDAAR كحزمة بايثون مباشرة في أي بيئة سحابية أو طرفية:

### التثبيت المباشر من GitHub:
```bash
# التثبيت الأساسي (مع دعم Google Gemini API و MkDocs)
pip install git+https://github.com/braadiv/MDAAR.git

# التثبيت مع دعم النماذج المحلية والتكميم 4-bit على GPU
pip install "mdaar[local] @ git+https://github.com/braadiv/MDAAR.git"
# أو تثبيت أدوات التسريع والتكميم يدوياً:
pip install -U transformers accelerate bitsandbytes
```

### الاستخدام البرمجي في كود بايثون:
```python
from mdaar_pipeline import MDAARPipeline, ModelGateway

# 1. إعداد بوابة الخطة (مثلاً Gemini) وبوابة التنفيذ والفحص (مثلاً Qwen2.5-Coder أوفلاين 4-bit)
planner_gateway = ModelGateway(
    provider="Gemini",
    model_name="gemini-2.5-flash",
    api_keys=["KEY_1", "KEY_2"]
)

executor_gateway = ModelGateway(
    provider="Local-Offline",
    local_model_path="Qwen/Qwen2.5-Coder-7B-Instruct",
    quantization="4bit", # يستهلك فقط ~5.5GB من VRAM على كارت T4
)

# 2. تهيئة خط الإنتاج المزدوج
pipeline = MDAARPipeline(
    project_name="ECommerceApp",
    git_source_url="https://github.com/myorg/ecommerce.git",
    git_docs_url="git@github.com:myorg/central-docs.git",
    source_auth="HTTPS",
    docs_auth="SSH",
    planner_gateway=planner_gateway,
    executor_gateway=executor_gateway,
)

# 3. مزامنة وتشغيل الفحص الكامل 100% + الخطة + التوليد + بوابة الجودة + فهرس RAG
current_commit, changed = pipeline.sync_source_repo()
stats = pipeline.run_multi_agent_generation()
print("Run Summary:", stats)

# 4. سؤال فهرس RAG الدائم في project_dir/.rag_index/
answer = pipeline.get_chat_engine().ask("ما هي الجداول ونقاط الـ API الرئيسية؟", audience="Developer")
print(answer)
```

---

## 🚀 تشغيل الدفاتر التفاعلية في كولاب (عربي / English)

يمكنك فتح الدفتر التفاعلي مباشرة في Google Colab بنقرة زر واحدة:

| الدفتر | الوصف | رابط الفتح المباشر |
|:---|:---|:---:|
| **documentation_pipeline.ipynb** | النسخة التفاعلية باللغة العربية مع نماذج Forms | [![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/braadiv/MDAAR/blob/main/documentation_pipeline.ipynb) |
| **documentation_pipeline_en.ipynb** | Full Interactive English Version with Forms | [![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/braadiv/MDAAR/blob/main/documentation_pipeline_en.ipynb) |

---

## 🗂️ هيكلة مجلدات المشروع في Google Drive

```text
/MyDrive/AI_Documentation_Workspace/<ProjectName>/
├── .config.json        # إعدادات المشروع وروابط Git
├── .state.json         # تتبع آخر Commit وتاريخ آخر تحديث
├── .rag_index/         # فهرس Hybrid BM25 RAG + بطاقات الكود + master_plan.md
├── .ssh/               # مفاتيح SSH المزدوجة (id_ed25519_source & id_ed25519_docs)
├── source_repo/        # الكود المصدري المستنسخ
└── docs_repo/          # مستودع وبوابة التوثيق التفاعلية
    ├── mkdocs.yml      # إعدادات ثيم Material والبحث والمخططات
    └── docs/
        ├── index.md    # الصفحة الرئيسية
        ├── 00_Governance/
        │   ├── Documentation-Policy.md
        │   ├── Documentation-Standards.md
        │   ├── Glossary.md
        │   ├── AI-Governance.md
        │   ├── Document-Register.md
        │   └── Quality-Audit-Report.md
        ├── 01_Administrative/
        │   ├── Project-Charter.md
        │   ├── Business-Case.md
        │   ├── Scope.md
        │   ├── BRD.md
        │   ├── Risk-Register.md
        │   └── Decision-Log.md
        ├── 02_Technical/
        │   ├── SRS.md
        │   ├── SAD.md
        │   ├── HLD.md
        │   ├── LLD.md
        │   ├── Database-ERD.md
        │   ├── Data-Dictionary.md
        │   ├── API-Reference.md
        │   └── Integrations.md
        ├── 03_Operations/
        │   ├── Installation-Guide.md
        │   ├── Configuration-Guide.md
        │   ├── Deployment-Guide.md
        │   ├── Security-Guide.md
        │   ├── Troubleshooting.md
        │   └── Backup-Recovery.md
        ├── 04_User-Guides/
        │   ├── User-Manual.md
        │   ├── Admin-Manual.md
        │   └── FAQs.md
        └── 05_Release-Documentation/
            └── Release-Notes.md
```

---

## 🧩 منظومة الوكلاء والمحللات المتخصصة (v2.0)

| المكون | التخصص والمسؤولية |
|:---|:---|
| **RepoScanner & SemanticFileChunker** | قراءة 100% من ملفات الكود غير المستثناة وتقسيم الملفات الضخمة بدون فقدان سطر واحد. |
| **SecretSanitizer & IgnoreMatcher** | استبعاد `vendor`/`node_modules`/`.gitignore` وتعقيم مفاتيح API وكلمات المرور قبل أي معالجة. |
| **PolyglotFactExtractor** | استخلاص حتمي للكلاسات والدوال والاعتماديات (PHP, Python AST, JS/TS, Java, C#, Go, Rust, Dart) + RBAC والأحداث. |
| **SchemaParser & APISpecParser** | استخلاص جداول SQL وهجرات Yii2/Laravel/Prisma ➔ Mermaid ERD، ونقاط API من Postman/OpenAPI/Yii2/FastAPI/Express. |
| **DualModelManager (Planner + Executor)** | تنسيق نموذج الخطة ونموذج التنفيذ مع تفريغ تلقائي لذاكرة GPU VRAM عند التبديل بين نموذجين محليين. |
| **MapperAgent & PlannerAgent** | فحص كل ملفات المشروع (`Map`) ثم بناء المخطط الهندسي الموحد (`Planner Blueprint`). |
| **Doc Agents (Arch/DB/API/Ops/User/Gov)** | توليد جميع وثائق الحزم الأربع بدعم مهارات MDAAR المدمجة وبروتوكول `[TBD]` / `[⚠️ BLIND SPOT]`. |
| **QualityAgent** | تدقيق Mermaid، كشف الأسماء غير الحقيقية، وتحديث `Document-Register.md` (`L0` ➔ `L1`) و`Quality-Audit-Report.md`. |
| **DocumentationChatEngine (Hybrid RAG)** | محرك BM25 + تطبيع العربية وتجزئة المعرفات البرمجية، مخزن بشكل دائم في `.rag_index/` مع عزل مستوى الجمهور. |

---

## 📄 الإطار المرجعي الشامل (plan.md)

يحتوي ملف [`plan.md`](plan.md) في هذا المستودع على الإطار المرجعي الشامل لهندسة التوثيق البرمجي المدعوم بالذكاء الاصطناعي (AI-Driven Software Documentation Framework). يمثل هذا الملف الأساس المنهجي المعتمد لجميع القوالب والمبادئ الهندسية ومصفوفات الحوكمة المطبقة في هذا المشروع.

---

## 📜 الترخيص (License)

هذا المشروع مرخص تحت رخصة **MIT License** - راجع ملف [LICENSE](LICENSE) للمزيد من التفاصيل.
