# 知识库字段模型、版本时效与会员权限设计

本文件定义「行业协会政策法规知识库」的文档结构、标签体系、版本/时效管理、会员分级权限，以及采编工作流。
目标是让**检索结果可溯源、时效可管控、内容受权限约束**，并直接指导入库与 RAG 切分。

---

## 一、文档类型（doc_type 枚举）

| 类型 | 说明 | 示例 |
|---|---|---|
| POLICY | 政策（宏观指导性文件） | 行业发展规划、指导意见 |
| REGULATION | 法规（具强制力） | 条例、办法、规定 |
| STANDARD | 准则 / 标准（行业自律） | 行为准则、技术规范 |
| CASE_POSITIVE | 正面案例 | 合规标杆、优秀实践 |
| CASE_NEGATIVE | 负面案例 | 违规通报、警示案例 |
| ENFORCEMENT | 执法通告 | 处罚决定、执法公告 |

---

## 二、核心字段模型（所有文档通用）

建议存于 PostgreSQL（元数据 + BM25 检索），向量存于 Qdrant（语义检索），通过 `doc_id` 关联。

| 字段 | 类型 | 说明 |
|---|---|---|
| `doc_id` | UUID | 主键 |
| `title` | string | 标题 |
| `doc_type` | enum | 见上 |
| `doc_number` | string | 文号（唯一标识，便于溯源） |
| `category` | string | 一级行业分类 |
| `sub_category` | string | 二级分类 |
| `issuing_authority` | string | 发布机构 |
| `jurisdiction` | string | 适用地域（全国/省/市） |
| `effective_date` | date | 生效日期 |
| `expiry_date` | date? | 废止/失效日期（null = 现行有效） |
| `status` | enum | `ACTIVE` / `REPEALED` / `SUPERSEDED` |
| `supersedes` | UUID[] | 本文件替代了哪些旧规 |
| `superseded_by` | UUID[] | 本文件被哪些新规替代 |
| `version` | string | 版本号（如 2024-修订版） |
| `tags` | string[] | 标签（见标签体系） |
| `access_level` | enum | 见权限模型 |
| `source_url` / `source_file` | string | 出处原文链接或归档文件 |
| `body` | text | 清洗后正文（Markdown，保留条款层级） |
| `chunk_strategy` | enum | `by_clause`（按条款）/ `by_paragraph`（按段） |
| `embedding_model` | string | 如 `BGE-M3` |
| `created_at` / `updated_at` / `reviewed_at` | datetime | 采编/复核时间 |

**切分建议**：法规/通告按「条款（条/款/项）」切分，保留 `doc_number` + 条款编号，使回答能精确引用「第 X 条第 Y 款」。

---

## 三、标签体系（tags）

- **主题标签**：环保、安全生产、质量管理、反垄断、数据安全、消费者权益……
- **行业细分**：钢铁、化工、服务业……
- **处罚类型**：警告、罚款、吊销、列入失信……
- **适用对象**：会员单位 / 个人会员 / 全体企业 / 特定资质主体
- **时效标签**：`现行有效` / `即将到期` / `已废止`（可由 `status`+日期派生，不必手填）

---

## 四、版本与时效管理（关键）

1. **双向替代链**：新規发布时，填 `supersedes` 指向旧规，旧规自动置 `status=SUPERSEDED` 并填 `superseded_by`。
2. **检索过滤**：默认只召回 `status=ACTIVE` 且 `effective_date ≤ 今天 ≤ (expiry_date|∞)` 的文档。
3. **答案标注时效**：模型回答须连同「现行有效 / 已废止（被 XX 替代）」一并给出，避免会员引用失效旧规。
4. **临期预警**：定时任务扫描 `expiry_date` 在 90 天内的文档，提醒采编复核。
5. **审计**：每次状态变更留操作日志（谁、何时、原因）。

---

## 五、会员分级权限模型（access_level）

| 级别 | 含义 | 可见内容 |
|---|---|---|
| `PUBLIC` | 全体会员 | 公开法规、通用准则 |
| `TIER2` | 高级/认证会员 | 上述 + 细分领域内部准则 |
| `INTERNAL` | 协会内部人员 | 上述 + 未公开案例、内部通报 |
| `ADMIN` | 管理员 | 全部 + 管理后台 |

**实现**：检索召回后按「用户角色 ∩ 文档 access_level」过滤；无权限文档不进入召回与上下文，杜绝越权泄露。

---

## 六、采编工作流（采集 → 入库 → 复核）

```
① 采集      外部/内部来源汇入（爬虫/邮件/手动上传）
② 初审      来源合法性、去重
③ 清洗结构化 抽取字段、正文 Markdown 化、按条款切分
④ 分类打标   category/sub_category/tags/access_level
⑤ 版本关联   建立 supersedes / superseded_by，更新旧规 status
⑥ 合规审核   专人/法务确认无误
⑦ 入库向量化 写 PG + Qdrant（BGE-M3），建立索引
⑧ 定期复核   临期提醒、失效清理、季度质量抽查
```

每步建议记录责任人、时间、状态，形成可追溯的采编台账（也是合规审计的一部分）。

---

## 七、与检索/生成的衔接

- **混合检索**：Qdrant 向量（语义）+ PostgreSQL/ES 的 BM25（关键词，命中「条/款/项」原文术语）。
- **精排**：BGE-Reranker-v2-m3 对候选二次排序，提升「找对条款」准确率。
- **引用溯源**：回答必须附带 `doc_number` + 条款编号 + `source_url`，无法溯源则**拒答**，禁止编造。
