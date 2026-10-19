---
name: harness-public-sources-v1
description: 投研公开来源辅助：定位公告目录和真实PDF附件，读取已保存的财务JSON并形成随稿引用。与harness-stock-research-v3配套，不代替金融分析方法。
version: '1'
---

# 公开来源：从入口到随稿引用

本包只提供入口和摘录脚本，不下载数据、不推测证券身份、不认证来源或投资判断。公司、公告、比较、行业研究方法仍按需读取harness-stock-research-v3。脚本路径由skill_info返回；以下路径相对本包。

## 1. 查到原文入口，再读取

使用已核实的带交易所代码，例如SH688178。用code_exec或shell_exec运行脚本，然后将返回url交给web_fetch。不要用临时代码另行下载而绕过来源登记。

| 目的 | 脚本调用 |
| --- | --- |
| 指定公司公告目录 | `python scripts/source_urls.py notices --symbol SH688178 --start YYYY-MM-DD --end YYYY-MM-DD` |
| 已定位公告内容/附件 | `python scripts/source_urls.py notice --article <目录实际返回的art_code>` |
| 第三方财务原值 | `python scripts/source_urls.py finance --symbol SH688178` |
| 第三方公司概况线索 | `python scripts/source_urls.py profile --symbol SH688178` |

公告目录不是已读公告，也不保证穷尽。核对返回codes、名称、notice_date和标题，选择与问题有关的公告；有更多页时使用实际页号，不能把第一页当全部范围。内容接口返回的document_links来自实际JSON/JSONP字段，继续用web_fetch读取该PDF；不拼PDF文件名。财报按标题、证券和期间核对后阅读业务构成、盈利、风险等相关页面；已下载页不能冒充模型已读。

这些是第三方公开入口，不能冒充交易所接口。公司公告的转载原件、第三方财务汇总、媒体分析分别标注。API失败或返回空数据就报告具体缺口，不能换数字、猜URL或借此重发UNKNOWN。

## 2. 财务JSON按期间读取并生成引用

先web_fetch取得source_id及saved_source。查看实际字段和日期格式后选择，不预设字段存在或把null当0。

```sh
python scripts/read_json.py <saved_source> --where 'SECUCODE=688178.SH' 'REPORT_DATE=2026-06-30 00:00:00' --fields SECUCODE REPORT_DATE CURRENCY TOTALOPERATEREVE PARENTNETPROFIT KCFJCXSYJLR --limit 1
```

完整输出单个JSON回执。source_excerpts沿现有机制登记实际已读原文；sources可直接并入发布草稿sources数组，rows保留原字段与原值。脚本不翻译财务名称或推断单位；证券、期间、指标、币种及单位另依实际字段说明/财报核对。资料不足时不用猜测的单位绘图。

## 3. 计算和正文共用依据

在同一容器脚本中保留上述sources，计算输入引用实际source_id、原值、单位和期间；中间结果引用calculation索引。把正文、这些引用、计算和图表一起写入本轮发布JSON，再brief_publish。不要只把数字打印给模型，发布时却漏掉财务引用和算式。

只为有用、已核定的比较绘图，不把未知业务占比画成已知；需要业务占比或收入主体时继续读分部原文。扣非归母净利润不是主营业务利润，二者不能互换；财务与业务核对继续由原方法负责。本包不自动改正文，不证明模型的因果解释正确。
