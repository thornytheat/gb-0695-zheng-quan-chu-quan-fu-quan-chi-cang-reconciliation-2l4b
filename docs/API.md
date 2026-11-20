# 接口

`POST /api/run`：JSON中的csv文本，省略使用样例；或multipart字段file。最大5MB。

`POST /api/m1`至`POST /api/m7`：独立运行到指定模块，JSON结构同运行接口。

`GET /api/history`：历史批次。`POST /api/review`接收id、revision和decision（通过或退回）。`GET /api/evidence.csv`提供单一证据导出。

结构错误422，版本变化409，文件过大413；错误返回code与message。所有页面可从左侧七个模块入口访问。

`GET /api/runs/<id>`读取指定批次；页面与证据CSV通过run_id查询参数绑定批次，模块导航保留该参数。

JSON请求体必须是对象，数组、标量、null、损坏JSON及错误上传字段返回400，且不运行默认样例。

## 港股分红汇兑（独立流程，页面 `/fx`）

港股现金分红按港币宣告、境内客户人民币到账。批次状态机：待结汇 → 待对账 → 已对账 → 已发布；支付状态：待结汇 → 待支付 → 已付。

`POST /api/fx/batches`：创建批次，JSON宣告 `{action, security, ex_date, dividend_hkd, locked_rate, holdings:[{client, shares}]}`。action 为幂等键：相同宣告重跑复用原批次（`reused:true`），同一行动不同宣告返回409，绝不重复折算或重复支付。`GET /api/fx/batches` 列表，`GET /api/fx/batches/<id>` 每户应得港币、锁定/采用汇率、实付人民币、差异及合计勾稽。

`POST /api/fx/batches/<id>/rate`：`{rate}` 确认实际结汇汇率，逐户计算实付（整批总额到分后按最大余额法分配，与银行整批结汇一致）。待对账状态下重订汇率会清空已登记回款；已对账或已发布后汇率不可变更（409）。

`POST /api/fx/batches/<id>/bank-return`：`{amount}` 登记银行回款。与Σ实付一致则批次转为已对账，否则保留对账差异可见。汇率未确认时登记回款返回409。

`POST /api/fx/batches/<id>/publish`：仅已对账可发布，发布后全部支付为已付；重复发布幂等。`GET /api/fx/batches/<id>/evidence.csv` 导出每户明细与合计的证据文件。

状态不允许的变更返回409，未知批次404。
