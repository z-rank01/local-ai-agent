---
name: harness-public-sources-v3
description: 公开资料入口；复用v1目录和v2财务读取，补充有原始报价时间的行情读取。仅提取原字段，不计算估值或认证金融口径。
version: '3'
---

# 公开资料与行情

`research_begin.evidence_requests`的kind=quote、symbol=已核实的交易所代码，会沿原web_fetch及code_exec取得腾讯行情接口原值和时间。

直接脚本用法：`python scripts/source_urls.py quote --symbol SZ000651`。抓取后，`python scripts/read_quote.py <实际saved_source> SZ000651`返回原字段和精确source_excerpts；不执行服务端JavaScript。旧JSON字段读取继续使用harness-public-sources-v2，不复制实现。

报价时间与抓取时间分开；最新报价不能自动称为已完成交易日收盘价。价格单位、证券类型、股本日期和估值口径仍需核对。接口失败时保留错误，不能用无来源价格或数学常数替代实际行情。旧版本与用户修改不覆盖。
