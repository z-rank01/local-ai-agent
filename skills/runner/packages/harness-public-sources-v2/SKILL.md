---
name: harness-public-sources-v2
description: 公开取证适配v2：实际公告入口、完整保存页JSON读取、短字段引句与可用报告日期。复用v1入口，不覆写旧包。
version: '2'
---

# 公开来源：定位、读取、引用

本包随工作区安装，与v1及用户修改分别保留。金融含义、单位与业务判断仍按harness-stock-research-v3方法及股票侧规则核对。

## 获取

优先用research_begin.evidence_requests登记已选择的证券与需求，程序沿现有工具执行。需要手动入口时，`scripts/source_urls.py`复用已安装v1的URL构造器：notices、notice、finance、profile，参数与v1相同。公告编号取自实际目录，原件地址取自document_links，不猜编号/路径。目录不是公告全文。

## 财务JSON

使用实际web_fetch回执的saved_source。完整JSON在保存文件的`pages[].text`；`content`可能只是带分页标记的截断展示，不能对它做json.loads。

`python scripts/read_json.py <saved_source> --where 'SECUCODE=000651.SZ' 'REPORT_DATE=2025-12-31 00:00:00' --fields SECUCODE REPORT_DATE CURRENCY TOTALOPERATEREVE PARENTNETPROFIT --limit 1`

- 先看实际字段与日期，不猜字段。没有指定报告日期时，available_periods提供实际日期供下一次精确选择，首两行不代表全部期间。
- rows.values是字段值，raw_values保留字段原始JSON数值文本，避免重新抄写或浮点误差。missing_fields不补零，available_fields用于定位实际字段名，不认证金融含义。
- source_excerpts为选中字段及筛选身份的连续原句，保留实际页码、位置、哈希；sources可直接用于发布，同一id可列多条。不要手工拼成另一段JSON引句。
- 完整输出这个JSON回执，系统收到后才登记已读；available_periods只是目录，不证明所有期间及报告已读。
- 引用、算式、图表共用这些原值与出处。快报、正式报告、更正和第三方字段分别标注；API数字不能冒充本轮逐页阅读的公司财报。
