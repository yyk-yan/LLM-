# LLM-Document-Extraction-System

> 基于大语言模型的异构文档理解与结构化信息抽取系统，支持从多格式数据源文档中自动提取信息并填写 Excel 或 Word 模板。**v2.0 起内置多租户与权限管理。**

![Python](https://img.shields.io/badge/Python-3.8+-blue) ![License](https://img.shields.io/badge/License-MIT-green) ![LLM](https://img.shields.io/badge/LLM-DeepSeek-orange) ![RAG](https://img.shields.io/badge/RAG-ChromaDB-purple) ![OCR](https://img.shields.io/badge/OCR-PaddleOCR-red) ![Auth](https://img.shields.io/badge/Auth-JWT-blueviolet)

------

## 功能特性

- 📂 **多格式数据源支持**：.docx、.xlsx、.md、.txt、.pdf
- 📋 **模板自动填表**：支持 .xlsx 和 .docx 格式
- 🤖 **AI 智能抽取**：基于大语言模型的语义理解与结构化信息提取
- 🔍 **RAG 字段映射**：基于向量检索的字段映射知识库，系统越用越准
- 📄 **PDF 智能路由**：文字层直接提取，扫描件自动切换 PaddleOCR 识别
- 🧠 **CoT 两阶段提取**：复杂文档先分析结构再定向提取，提升准确率
- 🚀 **Web 界面**：简洁易用的前端交互
- ⚡ **高效处理**：支持大文件分块处理和并发提取
- 🔐 **多租户与权限**：JWT 认证、邀请码注册、三级角色（超级管理员 / 租户管理员 / 成员），数据按租户与用户隔离
- ⚙️ **每用户独立 LLM 配置**：每个账号可独立设置自己的模型、API 地址、API Key，兼容任何 OpenAI Chat Completions 风格的接口（DeepSeek、阿里云百炼、硅基流动、OpenRouter 等）

## 系统要求

- Python 3.8+
- pip

## 快速开始

### 1. 安装依赖

```bash
# cd 项目存放路径
cd LLM-Document-Extraction-System
# 安装依赖
pip install -r requirements.txt
```

### 2. 下载向量模型

RAG 字段映射模块需要本地向量模型，首次使用需手动下载：

从 [hf-mirror.com](https://hf-mirror.com/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2) 下载所有文件，放置到以下路径：

```
backend/models/minilm/
├── config.json
├── tokenizer.json
├── tokenizer_config.json
├── special_tokens_map.json
├── sentence_bert_config.json
├── modules.json
├── model.safetensors
└── 1_Pooling/
    └── config.json
```

或使用命令行下载（需能访问 HuggingFace）：

```bash
pip install huggingface_hub
python -c "
import os
os.environ['HF_ENDPOINT'] = 'https://hf-mirror.com'
from huggingface_hub import snapshot_download
snapshot_download('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2', local_dir='backend/models/minilm')
"
```

### 3. 配置 LLM 接口与认证密钥

LLM 接口（**模型 / API 地址 / API Key**）支持两种配置方式，二选一即可：

**方式 A（推荐）：每用户登录后在 Web 界面里配置**

启动服务后，每个用户登录系统，在主界面点「⚙️ LLM 设置」按钮，独立填写自己的：

- 模型名（model）
- API 地址（base_url，必须是完整的 chat completions 路径）
- API Key

适合多人共用一台部署、每人用各自 LLM 账号的场景。

**方式 B：服务器环境变量**

适合个人独占部署或为所有用户提供一个统一兜底。在启动后端的同一终端窗口执行：

**Windows (PowerShell):**

```powershell
$env:DEEPSEEK_API_KEY = "your-api-key-here"
$env:DEEPSEEK_URL     = "https://api.deepseek.com/chat/completions"
$env:DEEPSEEK_MODEL   = "deepseek-chat"
```

**Windows (CMD):**

```cmd
set DEEPSEEK_API_KEY=your-api-key-here
set DEEPSEEK_URL=https://api.deepseek.com/chat/completions
set DEEPSEEK_MODEL=deepseek-chat
```

**Linux/Mac:**

```bash
export DEEPSEEK_API_KEY="your-api-key-here"
export DEEPSEEK_URL="https://api.deepseek.com/chat/completions"
export DEEPSEEK_MODEL="deepseek-chat"
```

> 当用户在 Web 界面填了对应字段时，会以用户级配置优先；用户没填的字段才回退到环境变量。

**常见 API 地址参考**

| 服务商 | base_url |
|---|---|
| DeepSeek 官方 | `https://api.deepseek.com/chat/completions` |
| 阿里云百炼（DashScope） | `https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions` |
| 硅基流动 | `https://api.siliconflow.cn/v1/chat/completions` |
| OpenRouter | `https://openrouter.ai/api/v1/chat/completions` |

可选：自定义 JWT 与初始管理员账号（强烈建议在生产环境设置）

```powershell
$env:JWT_SECRET             = "use-a-long-random-string"
$env:JWT_TTL_HOURS          = "12"
$env:INITIAL_ADMIN_USERNAME = "root"
$env:INITIAL_ADMIN_EMAIL    = "root@yourcompany.com"
$env:INITIAL_ADMIN_PASSWORD = "your-strong-password"
```

### 4. 启动服务

> 若失败可尝试在设置 API Key 的同一终端窗口启动后端

```bash
cd backend
python main.py
```

服务将在 `http://localhost:8000` 启动

**首次启动会自动**：

- 在 `backend/auth.db` 创建 SQLite 数据库（租户 / 用户 / 邀请码三张表）
- 创建 `default` 默认租户
- 创建初始超级管理员账号（终端会打印用户名/邮箱/密码，未通过环境变量自定义时默认 `admin / admin@example.com / admin123`）
- 把旧的 `backend/uploads/`、`outputs/` 中的零散文件迁入 `<租户>/legacy/`
- 把旧 RAG 知识库中的字段映射归入默认租户

⚠️ 生产环境请务必：① 修改默认密码；② 设置随机的 `JWT_SECRET`。

### 5. 打开 Web 界面

在浏览器中访问：`http://localhost:8000`

首次访问会进入登录页面：

1. 用初始超级管理员账号登录
2. 进入「管理」面板可创建新租户、签发邀请码
3. 把邀请码发给同事，让其在登录页的「使用邀请码注册」标签页注册

## 使用流程

### 步骤 0：登录或邀请码注册

- 访问首页会自动跳转到登录页
- 已有账号：用「用户名/邮箱 + 密码」登录
- 没有账号：管理员签发邀请码 → 在「使用邀请码注册」中填写邀请码 + 用户名 + 邮箱 + 密码即可加入对应租户
- 顶栏会显示「用户名 @ 租户名 + 角色标签」，并提供「填表 / 管理 / 改密 / 退出」入口

### 步骤 1：（首次使用）配置 LLM 接口

如果服务器没有为你预设环境变量，登录后第一次填表前先去主界面「⚙️ LLM 设置」按钮，填写本人专属的模型 / API 地址 / API Key。配置后端会保存到本人账号下，下次登录直接生效。

### 步骤 2：上传数据源文档

- 点击或拖拽上传包含数据的文档（支持多个）
- 系统自动提取文本内容（数据源**仅当前用户可见**）
- 显示已上传文件数量和字符数
- ⚡ 记得点击「上传并提取文本」，再次上传时先点击「清空数据源」

### 步骤 3：上传模板并填表

- 上传需要填写的模板文件（.xlsx 或 .docx）
- 可选：输入用户要求（如日期范围、特定条件等）
- 点击「开始填表」按钮
- 系统自动分析数据源并填写模板，RAG 知识库**按租户隔离**复用历史映射
- 下载填写完成的文件（仅本人可下载）

### 步骤 4（管理员）：管理租户与成员

- **租户管理员**：在「管理」面板生成邀请码、停用 / 启用本租户成员、调整成员角色
- **超级管理员**：在「管理」面板创建新租户（可同时签发该租户的管理员邀请码）、跨租户查看用户

## 项目结构

```
LLM-Document-Extraction-System/
├── backend/
│   ├── main.py              # FastAPI 主入口（接入认证 + 按用户隔离）
│   ├── auth.py              # JWT 签发/校验、密码哈希、依赖注入
│   ├── db.py                # SQLAlchemy 模型 Tenant/User/Invitation/UserSetting + 启动建库
│   ├── tenant_admin.py      # /api/auth/* 与 /api/admin/* 路由
│   ├── agent.py             # AI 数据提取与字段映射逻辑（按租户透传 tenant_id）
│   ├── extractor.py         # 文档解析模块（含 PDF 智能路由）
│   ├── filler.py            # 模板填写模块
│   ├── rag_mapper.py        # RAG 字段映射知识库（按租户隔离）
│   ├── auth.db              # SQLite 数据库（首次启动自动生成）
│   ├── models/              # 本地向量模型（需手动下载，见上方说明）
│   ├── field_mapping_db/    # ChromaDB 向量数据库（自动生成）
│   └── uploads/             # 上传文件，按 <租户slug>/u<用户id>/ 隔离
├── frontend/
│   ├── index.html           # Web 界面（含登录/注册/管理面板）
│   └── app.js               # 前端交互逻辑（带 token 持久化）
├── outputs/                 # 输出文件，按 <租户slug>/u<用户id>/ 隔离
├── requirements.txt         # Python 依赖
└── README.md
```

## 核心模块说明

### auth.py（新增）

- `hash_password()` / `verify_password()`：bcrypt 密码哈希与校验
- `create_access_token()` / `decode_token()`：JWT 签发与校验
- `get_current_user()`：FastAPI 依赖，校验 Bearer Token 并返回 User
- `require_role()`：角色守卫工厂（限定 super_admin / tenant_admin / member）

### db.py（新增）

- `Tenant` / `User` / `Invitation` / `UserSetting`：SQLAlchemy ORM 模型
- `init_db()`：建表 + 创建默认租户 + 创建初始超级管理员
- `make_invitation()`：生成带过期时间的一次性邀请码

### tenant_admin.py（新增）

- `/api/auth/login`、`/api/auth/register`、`/api/auth/me`、`/api/auth/change-password`
- `/api/auth/llm-settings` GET / PUT：每用户独立的 LLM 配置（model / base_url / api_key）
- `/api/admin/tenants`、`/api/admin/invitations`、`/api/admin/users`

### agent.py

- `_call()`: 调用 LLM API（流式输出 + 重试机制，支持 per-user 配置透传）
- `_resolve_llm_config()`: 用户配置 → 环境变量两级回退
- `_parse_json()`: 解析 API 返回的 JSON（容错处理）
- `_analyze_keywords()`: 分析用户要求和模板结构，生成过滤条件
- `_filter_text()`: 按关键词过滤文本行
- `_extract()`: 从文本块中提取结构化数据（强化格式约束）
- `_extract_with_cot()`: Chain-of-Thought 两阶段提取（复杂文档）
- `_align_headers_with_rag_and_llm()`: RAG 优先的三级字段匹配策略（按租户隔离）
- `extract_and_fill()`: 主流程函数（新增 `tenant_id` 参数透传到 RAG）

### extractor.py

- `extract_file()`: 通用文件提取（自动路由）
- `extract_text_from_pdf()`: PDF 智能提取（文字层/扫描件自动判断）
- `extract_scanned_pdf()`: PaddleOCR 扫描件识别
- `extract_pdf_with_tables()`: PDF 表格结构化提取
- `extract_text_from_docx()`: Word 文档提取
- `extract_text_from_xlsx()`: Excel 文件提取
- `get_xlsx_structure()`: 获取 Excel 模板结构
- `get_docx_structure()`: 获取 Word 模板结构

### rag_mapper.py

- `add_mapping(source, target, tenant_id)`: 记录字段映射对到向量知识库（带租户标签）
- `retrieve_mapping(query, tenant_id)`: 检索历史相似映射（仅本租户范围内）
- `get_stats(tenant_id)`: 查看（指定租户的）知识库统计信息
- `migrate_legacy_to_default()`: 启动时把无标签历史记录归入默认租户

### filler.py

- `fill_xlsx()`: 填写 Excel 模板（格式无损）
- `fill_docx()`: 填写 Word 模板（格式无损）

## API 接口

> v2.0 起，**除登录与注册外的所有 `/api/*` 都需要 `Authorization: Bearer <token>` 请求头**。

### 认证与管理

| 方法   | 路径                            | 权限                          | 说明                                 |
| ------ | ------------------------------- | ----------------------------- | ------------------------------------ |
| POST   | /api/auth/login                 | 公开                          | 登录，返回 access_token              |
| POST   | /api/auth/register              | 公开（须邀请码）              | 凭邀请码加入对应租户                 |
| GET    | /api/auth/me                    | 已登录                        | 当前用户信息                         |
| POST   | /api/auth/change-password       | 已登录                        | 修改密码                             |
| GET    | /api/auth/llm-settings          | 已登录                        | 读取本人 LLM 配置（API Key 不回显） |
| PUT    | /api/auth/llm-settings          | 已登录                        | 保存/更新本人 LLM 配置（model / base_url / api_key） |
| GET    | /api/admin/tenants              | super_admin                   | 列出所有租户                         |
| POST   | /api/admin/tenants              | super_admin                   | 创建租户（可同时签发管理员邀请）     |
| GET    | /api/admin/invitations          | tenant_admin / super_admin    | 列出邀请码                           |
| POST   | /api/admin/invitations          | tenant_admin / super_admin    | 创建邀请码                           |
| GET    | /api/admin/users                | tenant_admin / super_admin    | 列出用户（tenant_admin 仅本租户）    |
| PATCH  | /api/admin/users/{id}           | tenant_admin / super_admin    | 修改角色或启用/停用                  |

### POST /api/auth/login

**请求：** application/json

```json
{ "username_or_email": "admin", "password": "admin123" }
```

**响应：**

```json
{
  "access_token": "eyJhbGciOi...",
  "token_type": "bearer",
  "user": {
    "id": 1,
    "tenant_slug": "default",
    "tenant_name": "默认租户",
    "username": "admin",
    "email": "admin@example.com",
    "role": "super_admin"
  }
}
```

### POST /api/upload-sources

上传数据源文档（按当前用户隔离）

**请求：** multipart/form-data，files 字段包含多个文件，请求头需带 `Authorization: Bearer <token>`

**响应：**

```json
{
  "uploaded": 2,
  "total_sources": 2,
  "details": [
    {
      "filename": "data.xlsx",
      "status": "成功",
      "chars": 5000
    }
  ]
}
```

### POST /api/fill-template

上传模板并自动填表

**请求：** multipart/form-data，需带 Authorization 头

- `template`: 模板文件
- `requirement`: 用户要求（可选）

**响应：**

```json
{
  "status": "成功",
  "output_file": "filled_abc123_template.xlsx",
  "elapsed_seconds": 12.5,
  "download_url": "/api/download/filled_abc123_template.xlsx",
  "rag_hits": [
    { "target": "姓名", "source": "员工姓名", "similarity": 0.93 }
  ],
  "rag_hit_count": 1
}
```

### GET /api/download/{filename}

下载填写完成的文件（**仅本人可下载**，需带 Authorization 头）

### GET /api/sources

查看已上传的数据源列表（仅本人可见）

### DELETE /api/sources

清空当前用户的数据源（同时清空 `uploads/<slug>/u<id>/`）

## 多租户与权限模型

### 角色

| 角色            | 能力                                                                |
| --------------- | ------------------------------------------------------------------- |
| `super_admin`   | 系统级。可创建租户、跨租户查看用户、签发任意角色邀请码              |
| `tenant_admin`  | 仅本租户。可签发本租户邀请码、管理本租户成员（不可创建超级管理员）  |
| `member`        | 仅本人。可上传数据源、填表、下载自己的输出                          |

### 数据隔离

- **数据源（内存）**：按 `(tenant_id, user_id)` 拆分，互不可见
- **上传文件**：`backend/uploads/<tenant_slug>/u<user_id>/`
- **输出文件**：`outputs/<tenant_slug>/u<user_id>/`，下载需要带 token，且只能下载本人目录的文件
- **RAG 知识库**：单 collection，写入时打 `tenant_id` metadata，查询时按 `tenant_id` 过滤；不同租户的字段映射不会串扰

### 邀请码工作流

1. 管理员在「管理」面板里选择角色、可选限定邮箱、有效期，点击「生成邀请码」
2. 把邀请码字符串发给同事
3. 同事在登录页选「使用邀请码注册」，填邀请码 + 个人信息完成注册
4. 邀请码一次性使用，过期后自动失效

### 个人 LLM 配置（每用户独立）

- 顶栏「设置」打开本人专属的 LLM 配置面板，可独立保存 `model / base_url / api_key`
- 三项均为可选：缺哪一项就回退到服务器环境变量（`DEEPSEEK_MODEL / DEEPSEEK_URL / DEEPSEEK_API_KEY`）
- API Key 出于安全考虑保存后不再明文回显；提交时输入框留空表示"不修改"，输入新值则覆盖
- 点击「仅清除 API Key」可让该用户回退使用服务器默认 Key
- 兼容任何 OpenAI Chat Completions 风格的接口（DeepSeek 官方、阿里云百炼、硅基流动、OpenRouter 等）；`base_url` 必须填到 `/chat/completions` 完整路径，因为后端用 `requests.post()` 直发，不是 SDK

## 可能遇到的问题与解决办法

### Q: API Key 在哪里获取？

A: 访问 [DeepSeek 官网](https://platform.deepseek.com/) 注册账户并获取 API Key

### Q: 支持哪些文件格式？

A:

- 数据源：.docx、.xlsx、.xls、.md、.txt、.pdf
- 模板：.xlsx、.xls、.docx

### Q: PDF 识别效果不好怎么办？

A: 系统会自动判断 PDF 类型。若为扫描件会切换 PaddleOCR 识别，首次使用需安装 paddleocr==2.9.1。若识别仍有问题，检查是否在代码顶部设置了 `FLAGS_use_mkldnn=0` 禁用 oneDNN。

### Q: 向量模型加载失败怎么办？

A: 确认模型文件已下载到 `backend/models/minilm/` 目录，且 `rag_mapper.py` 中路径配置正确。

### Q: 如何处理大文件？

A: 系统自动对超过 50000 字符的 Excel 文件走结构化过滤路径，避免全文送 LLM 造成 token 超限。

### Q: 填表失败怎么办？

A:

1. 检查 API Key 是否正确设置（建议在同一终端窗口设置后启动）
2. 检查网络连接，确保能访问 DeepSeek API
3. 查看后端终端日志获取详细错误信息
4. 确保模板文件格式正确

### Q: 忘记初始管理员密码怎么办？

A: 删除 `backend/auth.db` 后重启服务会重新生成默认管理员；或在启动前用环境变量 `INITIAL_ADMIN_PASSWORD` 强制覆盖（仅在数据库中尚无超管时生效）。

### Q: 邀请码失效或丢失？

A: 在「管理 → 邀请码」中查看所有邀请码状态，已过期或已使用的可重新签发；邀请码有效期默认 72 小时，可在生成时调整。

### Q: 如何升级老版本（v1.x）？

A: 直接覆盖代码并重启即可。系统会自动：① 创建 `auth.db`；② 把 `uploads/`、`outputs/` 中遗留文件迁到 `<default>/legacy/`；③ 把旧 RAG 记录归入默认租户。原数据可用初始超级管理员账号访问到。

## 技术栈

- **后端**：FastAPI + Python 3.8+
- **认证**：JWT（PyJWT）+ bcrypt + SQLAlchemy（SQLite）
- **前端**：HTML5 + Vanilla JavaScript（无框架，原生 fetch + localStorage 管理 token）
- **文档处理**：python-docx、openpyxl、pdfplumber、PyMuPDF
- **OCR**：PaddleOCR
- **AI 服务**：DeepSeek API
- **RAG**：ChromaDB + Sentence Transformers（paraphrase-multilingual-MiniLM-L12-v2）
- **并发处理**：ThreadPoolExecutor

## 性能优化

- 大 Excel 文件走结构化过滤路径，跳过 LLM 全文读取
- 文本分块处理（2000 字符/块），并发提取（最多 6 个线程）
- RAG 向量检索优先，减少 LLM API 调用次数
- 智能关键词过滤，降低 token 消耗
- 复杂文档自动启用 CoT 两阶段提取，提升准确率
- 结果去重，避免重复数据
- RAG 知识库按租户标签过滤，多租户共享一份向量索引而互不串扰

## 注意事项

1. **API 配额**：DeepSeek API 有调用限制，请合理使用
2. **模型文件**：向量模型约 400MB，不包含在仓库中，需手动下载
3. **文件大小**：建议单个文件不超过 50MB
4. **隐私保护**：上传的文件按用户隔离存储在服务器，清空数据源时会同步清理本人目录
5. **网络要求**：需要能访问 DeepSeek API 服务，建议关闭代理后启动
6. **JWT 密钥**：生产环境请通过 `JWT_SECRET` 设置随机值；默认值仅供开发使用
7. **初始管理员**：首次启动会打印初始管理员密码，请尽快用「改密」面板修改
8. **邀请码安全**：邀请码相当于一次性入场凭证，请通过安全渠道发送给被邀请人
