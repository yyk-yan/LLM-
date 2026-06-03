"""
agent.py
系统核心处理模块，实现从数据源到填表数据的完整提取流程
包含关键词预分析、文本过滤、并发分块LLM提取、表头语义映射及三级字段匹配策略
支持大文件结构化过滤与小文件LLM提取的自适应差异化处理
"""
from __future__ import annotations
import json, os, re, requests, time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# ===== 新增：引入RAG字段映射模块 =====
from rag_mapper import add_mapping, retrieve_mapping
# ===== 新增结束 =====

for _k in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"]:
    os.environ.pop(_k, None)

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_URL = os.environ.get("DEEPSEEK_URL", "https://api.deepseek.com/chat/completions")
MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")


def _resolve_llm_config(cfg: dict | None) -> tuple[str, str, str]:
    """优先使用用户的个性化配置，缺项回退到环境变量"""
    cfg = cfg or {}
    api_key = (cfg.get("api_key") or "").strip() or DEEPSEEK_API_KEY
    base_url = (cfg.get("base_url") or "").strip() or DEEPSEEK_URL
    model = (cfg.get("model") or "").strip() or MODEL
    return api_key, base_url, model

"""
调用DeepSeek API
temperature=0.1接近0，意味着LLM每次输出几乎相同，适合结构化数据提取
DeepSeek不是一次性返回所有内容，而是像打字机一样一段一段发过来，每段都是 data: {...} 格式，代码把每段的 content 拼接起来，直到收到 [DONE]。
重试机制：网络不稳定时，自动等2秒重试，最多3次。
"""
def _call(sys_msg, usr_msg, max_tokens=2000, llm_cfg: dict | None = None):
    api_key, base_url, model = _resolve_llm_config(llm_cfg)
    if not api_key:
        raise RuntimeError("缺少 LLM API Key：请在「设置」面板填写，或在服务器上设置 DEEPSEEK_API_KEY 环境变量")
    h = {"Authorization": "Bearer " + api_key, "Content-Type": "application/json"}
    p = {"model": model, "temperature": 0.1, "max_tokens": max_tokens, "stream": True,
         "messages": [{"role": "system", "content": sys_msg}, {"role": "user", "content": usr_msg}]}
    for i in range(3):
        try:
            r = requests.post(base_url, headers=h, json=p, timeout=85, stream=True)
            r.raise_for_status()
            content = ""
            for line in r.iter_lines():
                if not line:
                    continue
                line = line.decode("utf-8") if isinstance(line, bytes) else line
                if line.startswith("data: "):
                    data = line[6:]
                    if data.strip() == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                        delta = chunk["choices"][0]["delta"].get("content", "")
                        content += delta
                    except Exception:
                        pass
            return content
        except Exception:
            if i == 2:
                raise
            time.sleep(2)

"""
解析LLM返回的JSON
LLM虽然被要求只返回JSON，但它经常会加上markdown代码块标记
这个函数就是专门应对这种情况的解析容错处理
"""
def _parse_json(text):
    text = text.strip()
    if "```" in text:
        parts = text.split("```")
        for part in parts:
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            if part.startswith("[") or part.startswith("{"):
                try:
                    return json.loads(part)
                except Exception:
                    pass
    try:
        return json.loads(text)
    except Exception:
        pass
    for ch in ["[", "{"]:
        idx = text.find(ch)
        if idx >= 0:
            try:
                return json.loads(text[idx:])   #从第一个[或{开始尝试解析
            except Exception:
                pass
    return []

"""
分析需要哪些关键词
把用户要求、模板结构、数据源样本一起给LLM。让它分析要填这个模板，应该从数据源里过滤出包含哪些关键词的行
"""
def _analyze_keywords(user_requirement, template_structure, source_samples="", llm_cfg: dict | None = None):
    """分析用户要求和模板，输出每个表格的结构化过滤条件"""
    template_desc = json.dumps(template_structure, ensure_ascii=False)
    sys_msg = "You are a data analyst. Output JSON only, no explanation."
    usr_msg = (
        "Analyze the user requirement and template structure. "
        "For each table key, output filter conditions to select relevant rows from the data source.\n"
        "User requirement: " + user_requirement + "\n"
        "Template structure: " + template_desc + "\n"
        + (f"Data source sample (first few rows):\n{source_samples}\n" if source_samples else "")
        + "Output JSON like:\n"
        "{\n"
        '  "table_0": {"match_all": ["city_name", "date_str"], "match_any": []},\n'
        '  "Sheet1": {"match_all": [], "match_any": ["2020-07-", "2020-08-"]}\n'
        "}\n"
        "Rules:\n"
        "- match_all: row must contain ALL of these strings (AND logic, for specific values like city+date)\n"
        "- match_any: row must contain ANY of these strings (OR logic, for date ranges)\n"
        "- For date ranges, look at the sample data to determine the actual date format, then generate correct prefix strings\n"
        "- If no filter needed, use empty lists\n"
        "Output JSON only."
    )
    raw = _call(sys_msg, usr_msg, max_tokens=800, llm_cfg=llm_cfg)
    result = _parse_json(raw)
    if isinstance(result, dict):
        return result
    return {}


def _filter_text(text, keywords):
    """步骤2：按关键词过滤文本行，保留表头和匹配行"""
    if not keywords:
        return text
    lines = text.split("\n")
    header_lines = []
    matched_lines = []
    # 找表头（前5行中含 | 的行）
    for line in lines[:5]:
        if "|" in line:
            header_lines.append(line)
    # 过滤匹配行
    for line in lines:
        if any(kw in line for kw in keywords):
            matched_lines.append(line)
    result = "\n".join(header_lines + matched_lines)
    print(f"  过滤: {len(text)} -> {len(result)} 字符 ({len(matched_lines)} 行匹配)")
    return result if matched_lines else text[:5000]  # 没匹配到则取前5000字


# ===== 改动：_extract 函数升级，强化结构化输出约束 =====
def _extract(chunk, headers, req, llm_cfg: dict | None = None):
    """
    步骤3：从文本块中提取符合表头的数据行
    升级点：
    1. system prompt明确要求输出必须以[开头、]结尾，减少LLM加多余文字的概率
    2. 明确的格式归一化规则（日期/金额/电话/null处理）
    3. 多值冲突时有明确优先级规则
    """
    sys_msg = (
        "You are a data extraction expert. "
        "Output JSON array only. No explanation, no markdown, no extra text. "
        "Every response must start with [ and end with ]."
    )
    usr_msg = (
        "Extract all data rows matching these fields from the text below.\n"
        "Fields: " + json.dumps(headers, ensure_ascii=False) + "\n"
        "User requirement: " + req + "\n"
        "\n"
        "## Normalization rules\n"
        "1. First infer the real meaning of each field from the template field name, surrounding table context, and document context. Do not rely only on the literal field name.\n"
        "2. For vague or inconsistent fields, output the value that best matches the business meaning, not the shortest value in the source text.\n"
        "3. For region-related fields (地区, 省份, 所在地, 区域, 行政区划):\n"
        "   - Prefer a standard full administrative division name.\n"
        "   - If the template context indicates a provincial-level region, normalize to '中国 + province/autonomous region/municipality full name'.\n"
        "   - Examples: 广东 -> 中国广东省; 湖北 -> 中国湖北省; 中国广东 -> 中国广东省.\n"
        "   - If other fields have clear implied values from already known information, you may complete them consistently.\n"
        "   - If only the country is present but the surrounding context clearly requires a province-level region, infer it from context when reliable; otherwise return null.\n"
        "4. For time, units, numbers, and names, keep the original meaning unchanged and normalize the format only; do not rewrite the business meaning.\n"
        "5. If multiple candidate values exist across sources for the same field, prefer the one that best matches the template context, is most complete, and is most standardized.\n"
        "6. If the field cannot be determined reliably, output null; do not invent data.\n"
        "\n"
        # ===== 新增：强化格式约束 =====
        "## Output format constraints\n"
        "- Return ONLY a JSON array. Never add any text before [ or after ].\n"
        "- For uncertain fields: use null (JSON null), NOT empty string, NOT '未知', NOT '无'.\n"
        "- For dates: always format as YYYY-MM-DD. Examples: '2024年3月15日' -> '2024-03-15', '24/3/15' -> '2024-03-15'.\n"
        "- For amounts/numbers: keep digits and unit together. Examples: '壹千元' -> '1000元', '1,234.56' -> '1234.56'.\n"
        "- For phone numbers: digits only, no spaces or dashes. Example: '138-0000-1234' -> '13800001234'.\n"
        "- If the same field appears multiple times in the text, take the most recent or most complete value.\n"
        "- Field names in output must match the input Fields list exactly (same characters, same case).\n"
        # ===== 新增结束 =====
        "\n"
        "Text:\n" + chunk + "\n"
        "Output JSON array. Each element is an object with field names matching exactly. Output [] if no data."
    )
    raw = _call(sys_msg, usr_msg, max_tokens=8000, llm_cfg=llm_cfg)
    return _parse_json(raw)
# ===== 改动结束 =====


# ===== 新增：Chain-of-Thought版本，用于复杂文档（长文本/结构混乱的文档）=====
def _extract_with_cot(chunk, headers, req, llm_cfg: dict | None = None):
    """
    带Chain-of-Thought的提取函数
    适用场景：文本结构复杂、字段分散在多个段落、或普通_extract效果不好时
    两步走：
      Step1 - 让LLM先分析文档结构（哪段包含哪类信息）
      Step2 - 基于分析结果定向提取
    代价：多一次API调用，但准确率对复杂文档显著提升
    """
    # Step1：文档结构分析
    sys_msg_analyze = (
        "You are a document analyst. "
        "Analyze the structure of the given text and identify which paragraphs or sections "
        "contain which types of information. Be concise."
    )
    usr_msg_analyze = (
        "Analyze this document's structure. Identify which parts contain information relevant to these fields:\n"
        "Fields: " + json.dumps(headers, ensure_ascii=False) + "\n"
        "User requirement: " + req + "\n"
        "\n"
        "For each field, briefly note which section/paragraph likely contains it.\n"
        "Then summarize the document type and layout in 1-2 sentences.\n"
        "\n"
        "Document text:\n" + chunk
    )
    analysis = _call(sys_msg_analyze, usr_msg_analyze, max_tokens=600, llm_cfg=llm_cfg)
    print(f"  [CoT] 文档结构分析完成，基于分析结果提取...")

    # Step2：基于分析结果提取
    sys_msg_extract = (
        "You are a data extraction expert. "
        "Output JSON array only. No explanation, no markdown, no extra text. "
        "Every response must start with [ and end with ]."
    )
    usr_msg_extract = (
        "Based on the document structure analysis below, extract all data rows from the document.\n"
        "\n"
        "## Document structure analysis\n"
        + analysis + "\n"
        "\n"
        "## Fields to extract\n"
        + json.dumps(headers, ensure_ascii=False) + "\n"
        "\n"
        "## User requirement\n"
        + req + "\n"
        "\n"
        "## Output format constraints\n"
        "- Return ONLY a JSON array. Never add any text before [ or after ].\n"
        "- For uncertain fields: use null (JSON null), NOT empty string.\n"
        "- For dates: always format as YYYY-MM-DD.\n"
        "- For amounts/numbers: keep digits and unit together.\n"
        "- Field names in output must match the Fields list exactly.\n"
        "\n"
        "## Document text\n"
        + chunk + "\n"
        "\n"
        "Output JSON array only. Output [] if no data."
    )
    raw = _call(sys_msg_extract, usr_msg_extract, max_tokens=8000, llm_cfg=llm_cfg)
    return _parse_json(raw)
# ===== 新增结束 =====


"""
字段名标准化
把字段名里所有标点、空格、特殊符号都去掉，转小写。
"""
def _normalize_header(text):
    return re.sub(r"[\s\-_—–·|/\\:：,，.。()（）\[\]【】{}<>]+", "", str(text or "")).lower()


"""
LLM做字段映射（已升级为RAG+LLM三级策略）
"""
def _align_headers_with_llm(source_headers, target_headers, sample_rows=None, user_requirement="", tenant_id: int | None = None, llm_cfg: dict | None = None):
    sys_msg = "You are a data mapping expert. Output JSON only, no explanation."
    usr_msg = (
        "Map source spreadsheet headers to template headers.\n"
        "Source headers: " + json.dumps(source_headers, ensure_ascii=False) + "\n"
        "Template headers: " + json.dumps(target_headers, ensure_ascii=False) + "\n"
        + ("Sample rows from source sheet:\n" + json.dumps(sample_rows[:5], ensure_ascii=False) + "\n" if sample_rows else "")
        + ("User requirement: " + user_requirement + "\n" if user_requirement else "")
        + "Return a JSON object where keys are template headers and values are the matching source header names.\n"
        + "Rules:\n"
        + "- Prefer semantic matches over literal matches.\n"
        + "- If a template header is not represented in the source, use an empty string as the value.\n"
        + "- Do not invent source headers.\n"
        + "- If multiple source headers could match, choose the one most consistent with the template context and sample rows.\n"
        + "- Output JSON only."
    )
    raw = _call(sys_msg, usr_msg, max_tokens=1200, llm_cfg=llm_cfg)
    result = _parse_json(raw)
    if isinstance(result, dict):
        # ===== 改动：把LLM匹配结果按租户写入RAG知识库 =====
        if tenant_id is not None:
            for target_field, source_field in result.items():
                if source_field and str(source_field).strip():
                    add_mapping(source_field, target_field, tenant_id=tenant_id)
        # ===== 改动结束 =====
        return {str(k): str(v) for k, v in result.items() if str(k).strip()}
    return {}


def _align_headers_with_rag_and_llm(source_headers, target_headers, sample_rows=None, user_requirement="", rag_hits=None, tenant_id: int | None = None, llm_cfg: dict | None = None):
    """
    RAG优先的表头对齐策略（多租户版）
    所有 RAG 读写都带 tenant_id，租户之间永不串扰。
    若未传 tenant_id（理论上不应发生），跳过 RAG 直接走 LLM。
    """
    print(f"  [调试] 数据源表头: {source_headers}")
    print(f"  [调试] 模板表头: {target_headers}")

    header_map = {}
    matched_targets = set()

    if tenant_id is not None:
        for source_field in source_headers:
            history = retrieve_mapping(source_field, tenant_id=tenant_id, threshold=0.85)
            for record in history:
                tgt = record["target"]
                if tgt in target_headers and tgt not in matched_targets:
                    header_map[tgt] = source_field
                    matched_targets.add(tgt)
                    print(f"  [RAG命中] {tgt} → {source_field} (相似度{record['similarity']})")
                    if rag_hits is not None:
                        rag_hits.append({
                            "target": tgt,
                            "source": source_field,
                            "similarity": record["similarity"],
                        })
                    break
    else:
        print("  [警告] 未提供 tenant_id，跳过 RAG 检索")

    unmatched_targets = [t for t in target_headers if t not in matched_targets]
    if unmatched_targets:
        print(f"  [LLM匹配] 未命中字段数: {len(unmatched_targets)}，调用LLM...")
        llm_map = _align_headers_with_llm(source_headers, unmatched_targets, sample_rows, user_requirement, tenant_id=tenant_id, llm_cfg=llm_cfg)
        header_map.update(llm_map)

    print(f"  [调试] 最终字段映射: {header_map}")
    return header_map

"""
按映射关系搬数据
"""
def _map_rows(src_rows, target_headers, header_map=None):
    """按列名映射，优先使用表头语义映射，再做去空格模糊匹配"""
    result = []
    header_map = header_map or {}
    normalized_header_map = {_normalize_header(k): v for k, v in header_map.items() if str(v).strip()}
    for src_row in src_rows:
        mapped = {}
        normalized_src = {_normalize_header(sk): sv for sk, sv in src_row.items()}
        for th in target_headers:
            value = None
            mapped_source_header = header_map.get(th, "") or normalized_header_map.get(_normalize_header(th), "")
            if mapped_source_header:
                for sk, sv in src_row.items():
                    if sk == mapped_source_header or _normalize_header(sk) == _normalize_header(mapped_source_header):
                        value = sv
                        break
            if value is None:
                norm_th = _normalize_header(th)
                if norm_th in normalized_src:
                    value = normalized_src.get(norm_th)
            if value is None:
                th_clean = th.replace(" ", "")
                for sk, sv in src_row.items():
                    if sk.replace(" ", "") == th_clean:
                        value = sv
                        break
            if value is not None:
                mapped[th] = value
        if any(v is not None and v != "" for v in mapped.values()):
            result.append(mapped)
    return result


"""
主流程
"""
"""
主流程
"""
def extract_and_fill(source_texts, template_path, template_structure, user_requirement, source_paths=None, tenant_id: int | None = None, llm_cfg: dict | None = None):
    from extractor import extract_xlsx_rows, extract_pdf_rows   # 改动：多导入 extract_pdf_rows

    rag_hits = []   # 收集本次所有RAG命中明细

    has_large_file = any(
        (source_paths or {}).get(fname, "").lower().endswith((".xlsx", ".xls")) and (len(text) == 0 or len(text) > 50000)
        for fname, text in source_texts.items()
    )
    total_text_len = sum(len(t) for t in source_texts.values())

    keywords_map = {}
    if has_large_file or total_text_len > 15000:
        print("步骤1: 分析过滤关键词...")
        source_samples = ""
        for fname, text in source_texts.items():
            fpath = (source_paths or {}).get(fname, "")
            is_xlsx = fpath and Path(fpath).suffix.lower() in (".xlsx", ".xls")
            if is_xlsx and (len(text) == 0 or len(text) > 50000):
                try:
                    import openpyxl as _opx
                    _wb = _opx.load_workbook(fpath, data_only=True)
                    _ws = _wb.active
                    _lines = []
                    for _i, _row in enumerate(_ws.iter_rows(values_only=True)):
                        if _i >= 3: break
                        _lines.append(" | ".join(str(v) if v else "" for v in _row))
                    _wb.close()
                    source_samples += f"[{fname}]\n" + "\n".join(_lines) + "\n"
                except Exception:
                    pass
            elif text:
                source_samples += f"[{fname}]\n" + text[:300] + "\n"
        keywords_map = _analyze_keywords(user_requirement, template_structure, source_samples, llm_cfg=llm_cfg)
        print("  关键词:", keywords_map)
    else:
        print("步骤1: 文本较小，跳过关键词分析，直接提取")

    result = {}
    for key, data in template_structure.items():
        headers = data.get("headers", [])
        if not headers:
            continue

        all_rows = []

        for fname, text in source_texts.items():
            fpath = (source_paths or {}).get(fname, "")
            suffix = Path(fpath).suffix.lower() if fpath else ""
            is_xlsx = suffix in (".xlsx", ".xls")
            is_pdf = suffix == ".pdf"

            # 本表的过滤条件（xlsx / pdf 结构化路径共用）
            filter_cond = keywords_map.get(key, {})
            if isinstance(filter_cond, dict):
                match_all = filter_cond.get("match_all", [])
                match_any = filter_cond.get("match_any", [])
            else:
                match_all = filter_cond if filter_cond else []
                match_any = []

            # ===== 新增：PDF表格走结构化映射（与xlsx同一套），识别不到表格再回退文本 =====
            if is_pdf:
                source_headers, src_rows = extract_pdf_rows(fpath, match_all=match_all, match_any=match_any)
                if source_headers and src_rows:
                    header_map = _align_headers_with_rag_and_llm(source_headers, headers, src_rows, user_requirement, rag_hits=rag_hits, tenant_id=tenant_id, llm_cfg=llm_cfg)
                    rows = _map_rows(src_rows, headers, header_map=header_map)
                    print(f"  [{key}] {fname}: PDF表格结构化映射 {len(rows)} 行")
                    all_rows.extend(rows)
                    continue
                else:
                    print(f"  [{key}] {fname}: PDF未识别到表格，回退文本提取")
            # ===== 新增结束 =====

            if is_xlsx and (len(text) > 50000 or len(text) == 0):
                source_headers, src_rows = extract_xlsx_rows(fpath, match_all=match_all, match_any=match_any)
                header_map = _align_headers_with_rag_and_llm(source_headers, headers, src_rows, user_requirement, rag_hits=rag_hits, tenant_id=tenant_id, llm_cfg=llm_cfg)
                rows = _map_rows(src_rows, headers, header_map=header_map)
                print(f"  [{key}] {fname}: 直接映射 {len(rows)} 行")
                all_rows.extend(rows)
            else:
                if isinstance(filter_cond, dict):
                    kws = filter_cond.get("match_all", []) + filter_cond.get("match_any", [])
                else:
                    kws = filter_cond if filter_cond else []
                if len(text) > 10000:
                    filtered = _filter_text(text, kws)
                else:
                    filtered = text
                paragraphs = [p for p in filtered.split('\n') if p.strip()]
                chunks, cur = [], ""
                for p in paragraphs:
                    if len(cur) + len(p) > 2000 and cur:
                        chunks.append(cur)
                        cur = p
                    else:
                        cur += "\n" + p
                if cur:
                    chunks.append(cur)
                if not chunks:
                    chunks = [filtered]
                print(f"  [{key}] {fname}: {len(chunks)} 块")

                use_cot = (len(chunks) == 1 and len(filtered) > 1000)
                if use_cot:
                    print(f"  [{key}] {fname}: 检测到复杂文档，启用CoT提取模式")

                with ThreadPoolExecutor(max_workers=min(len(chunks), 6)) as executor:
                    if use_cot:
                        futures = [executor.submit(_extract_with_cot, c, headers, user_requirement, llm_cfg) for c in chunks]
                    else:
                        futures = [executor.submit(_extract, c, headers, user_requirement, llm_cfg) for c in chunks]
                    for future in as_completed(futures):
                        rows = future.result()
                        if isinstance(rows, list):
                            all_rows.extend(rows)

        seen = set()
        unique = []
        for row in all_rows:
            k = str(row)
            if k not in seen:
                seen.add(k)
                unique.append(row)
        result[key] = unique
        print(f"  [{key}] 提取到 {len(unique)} 行")

    return result, rag_hits