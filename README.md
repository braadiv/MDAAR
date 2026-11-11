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

## 🌟 الرؤية المعمارية للمنظومة

1. **مجانية 100% وبدون اشتراكات سحابية إضافية:**
   - تعمل كافة المعالجات الثقيلة وتوليد الوثائق على **Google Colab**، مما يوفر كامل موارد المعالج والذاكرة لجهازك المحلي.
   - دعم **Google Gemini API** المجاني مع خاصية **تدوير المفاتيح التلقائي (Key Rotation)**.
   - دعم كامل لتشغيل نماذج مفتوحة المصدر **أوفلاين 100%** داخل كولاب (`Qwen2.5-Coder`, `Gemma-2`) للخصوصية المطلقة.
2. **استدامة البيانات والحالة عبر Google Drive:**
   - حفظ تلقائي للمفاتيح، إعدادات المشروع، وتتبع آخر Commit في ملفات `.config.json` و `.state.json` في Google Drive لمنع فقدان البيانات بين جلسات كولاب.
3. **مفاتيح SSH مزدوجة ومستقلة تماماً:**
   - مفتاح Deploy Key خاص بمستودع الكود (`id_ed25519_source`).
   - مفتاح Deploy Key مستقل تماماً لمستودع التوثيق الخارجي (`id_ed25519_docs`).
4. **محللات متخصصة للمخططات وقواعد البيانات (Schema & Route Parsers):**
   - تحويل ملفات SQL DDL تلقائياً إلى مخططات **Mermaid ERD** حقيقية.
   - استخراج مسارات الكود ونقاط الاتصال إلى جدول منظم في **API Reference**.
5. **التوثيق الحي المستمر (Living Documentation):**
   - تتبع فروقات الكود عبر `git diff` وتحليل أثر التغيير (`ImpactAnalyzer`) لتحديث الوثائق المتأثرة فقط.
6. **مساعد معرفي ذكي تفاعلي (Interactive AI Q&A / RAG):**
   - استجواب الوثائق باللغة الطبيعية مباشرة داخل كولاب مع ذكر أسماء ملفات المصادر المستشهد بها مجاناً بالكامل.
7. **بوابة توثيق تفاعلية فورية (MkDocs Material + Cloudflare Tunnel):**
   - موقع تفاعلي يدعم اللغة العربية والوضع الليلي ومخططات Mermaid وبث مباشر عبر رابط إنترنت آمن.

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
pip install git+https://github.com/braadiv/MDAAR.git
```

### التثبيت السريع عبر سحب الملف المصدري داخل Colab:
```bash
!wget -q https://raw.githubusercontent.com/braadiv/MDAAR/main/mdaar_pipeline.py -O /content/mdaar_pipeline.py
```

### الاستخدام البرمجي في كود بايثون:
```python
from mdaar_pipeline import MDAARPipeline, ModelGateway

# 1. إعداد بوابة الذكاء الاصطناعي (Gemini أو Local-Offline)
gateway = ModelGateway(
    provider="Gemini",
    model_name="gemini-2.5-flash",
    api_keys=["YOUR_GEMINI_KEY_1", "YOUR_GEMINI_KEY_2"] # يدعم التدوير
)

# 2. تهيئة خط الإنتاج
pipeline = MDAARPipeline(
    project_name="ECommerceApp",
    git_source_url="https://github.com/myorg/ecommerce.git",
    git_docs_url="git@github.com:myorg/central-docs.git",
    source_auth="HTTPS",
    docs_auth="SSH",
    gateway=gateway
)

# 3. تشغيل استخلاص المخططات وبناء الهيكل
pipeline.init_framework_structure()
pipeline.extract_and_generate_specialized()

# 4. إطلاق البوابة التفاعلية
tunnel_url = pipeline.serve_and_tunnel()
print("Portal URL:", tunnel_url)
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
├── .config.json        # إعدادات المشروع وروابط Git والمفاتيح
├── .state.json         # تتبع آخر Commit وتاريخ آخر تحديث
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
        │   └── Document-Register.md
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
        │   └── API-Reference.md
        ├── 03_Operations/
        │   ├── Installation-Guide.md
        │   ├── Configuration-Guide.md
        │   ├── Deployment-Guide.md
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

## 🧩 منظومة الوكلاء والمحللات المتخصصة

| المكون | التخصص والمسؤولية |
|:---|:---|
| **ArchitectureAgent** | تحليل البنية المعمارية وتوليد وثائق SAD, HLD, LLD ومخططات Mermaid. |
| **DatabaseAgent** | توثيق الجداول وقواميس البيانات والعلاقات بين الكيانات. |
| **APIAgent** | استخراج وتوثيق نقاط الاتصال ومواصفات الطلب والاستجابة لـ REST APIs. |
| **OperationsAgent** | صياغة أدلة التثبيت والنشر CI/CD واستكشاف الأعطال والنسخ الاحتياطي. |
| **UserGuideAgent** | صياغة أدلة الاستخدام المبسطة للمستخدم النهائي ولمسؤولي النظام. |
| **QualityAgent** | تدقيق الاتساق وتحديث سجل الوثائق المعتمد (Document Register). |
| **SchemaParser** | قراءة ملفات SQL واستخلاص الجداول والمفاتيح الخارجية إلى Mermaid ERD. |
| **RouteParser** | قراءة مسارات الكود (FastAPI, Flask, Express, Laravel) إلى جداول Endpoints. |
| **ImpactAnalyzer** | تتبع الـ Git Diff وتوجيه الوكلاء لتحديث الوثائق المتأثرة بالتغيير فقط. |
| **DocumentationChatEngine** | محرك بحث واسترجاع دلالي خفيف (In-Memory RAG) للاستجواب الذكي. |

---

## 📄 الإطار المرجعي الشامل (plan.md)

يحتوي ملف [`plan.md`](plan.md) في هذا المستودع على الإطار المرجعي الشامل لهندسة التوثيق البرمجي المدعوم بالذكاء الاصطناعي (AI-Driven Software Documentation Framework). يمثل هذا الملف الأساس المنهجي المعتمد لجميع القوالب والمبادئ الهندسية ومصفوفات الحوكمة المطبقة في هذا المشروع.

---

## 📜 الترخيص (License)

هذا المشروع مرخص تحت رخصة **MIT License** - راجع ملف [LICENSE](LICENSE) للمزيد من التفاصيل.
