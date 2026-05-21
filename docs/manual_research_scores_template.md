# 人工研究评分说明

`deploy_data/manual_research_scores.csv` 用于把基金经理的基本面研究判断纳入 A1 项目质量分。

字段：
- `code`：正式代码或临时代码，可为空。
- `name`：公司简称，建议填写。
- `manual_quality_rating`：强 / 较强 / 中性 / 较弱 / 回避，或英文 Strong / Positive / Neutral / Weak / Avoid。
- `manual_quality_score`：0-100 分，如填写则优先使用。
- `industry_view`：行业观点。
- `valuation_view`：估值观点。
- `research_comment`：研究备注。
- `updated_by` / `updated_date`：更新人和日期。

上传路径：`deploy_data/manual_research_scores.csv`。
