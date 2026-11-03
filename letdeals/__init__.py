"""letdeals — LowEndTalk / LowEndBox VPS 优惠聚合工具。

模块划分（参考 hosting-deals-tracker 的工程结构）:

    config      运行配置（路径、阈值、站点与域名）
    http        带重试的 HTTP 抓取
    textutil    HTML 清洗、单位换算、表格重建
    extract     字段抽取（规格/价格/折扣/活动性质/地点）
    replies     回帖抓取与库存表态判定
    sources     数据源（LET Offers RSS / LowEndBox RSS）
    pipeline    编排：采集 → 抽取 → 记录
    render      渲染：Markdown + 静态站
    cli         命令行入口
"""

__version__ = "0.4.0"
