# 数据来源与第三方声明

iDeal 只把第三方内容当作“优惠线索”，价格、币种、评分、版本、开发者和 App
可用性均以运行时取得的 Apple 官方接口结果为准。第三方来源异常时，不会绕过
Apple 核验直接推送。

## Apple 官方服务

### Apple Marketing Tools RSS

- 地址：<https://rss.marketingtools.apple.com/>
- 用途：取得 CN、TR、US 的 Top Paid App 榜单，作为可能发生价格变化的候选池。
- 数据归属：Apple Inc.

### iTunes Search / Lookup API

- 文档：<https://performance-partners.apple.com/search-api>
- 接口：<https://itunes.apple.com/lookup>
- 用途：按 App ID 和区服批量核验当前价格、币种、评分、版本、开发者、系统要求
  与 App Store 链接。
- 使用方式：每个请求最多合并 100 个 App ID，并使用本地缓存和重试控制请求量。
- 数据归属：Apple Inc.；项目与 Apple 无关联，也未获得 Apple 背书。

Apple、App Store、iPhone 和相关标识是 Apple Inc. 的商标。Apple 返回的推广内容
及图标受 Apple 条款约束；iDeal 默认通知不复制或保存 App 图标、试听内容。

## appstore-discounts

- 项目：<https://github.com/appstore-discounts/appstore-discounts>
- 作者：Eyelly Wu 及项目贡献者
- 许可证：MIT
- 使用内容：CN、TR、US 的公开 RSS 降价线索。
- 传输回退：Fastly jsDelivr、jsDelivr、GitHub Raw。

jsDelivr 只是 RSS 的传输 CDN，不是数据创作者。iDeal 保留上游来源名称，并在
通知中显示实际证据来源。

## Freshapps

- 网站：<https://freshapps.com/>
- RSS：<https://freshapps.com/rss/free.xml>、
  <https://freshapps.com/rss/deals.xml>
- 用途：补充 US 区限免和降价线索。
- 说明：只读取网站公开提供的 RSS，不复制网页全文；通知保留来源并链接回
  App Store。未发现该数据源单独声明的开源许可证。

## 已移除来源

AppAgg、Reddit、AppDovo 和 AppsHunter 不再包含在默认配置或代码中。它们此前
均为关闭状态，且会增加授权、反爬策略、接口变动和内容准确性的维护成本。

## 免责声明

本项目代码采用 MIT 许可证，但 MIT 许可证不授予任何第三方数据、商标或内容的
权利。价格和可用性可能随区服及时间变化，请以实际 App Store 页面为准。
