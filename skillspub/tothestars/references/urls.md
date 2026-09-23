# 坐标换算 + 链接模板 + URL 编码

## 1. 坐标换算

### 时角式 → 十进制度

- `DEC = ±(度 + 分/60 + 秒/3600)`（南纬为负）
- `RA(度) = (时 + 分/60 + 秒/3600) × 15`（1h = 15°）

例：`23:47:06.00 +29:29:17.3`
- Dec = 29 + 29/60 + 17.3/3600 = **+29.48814°**
- RA = (23 + 47/60 + 6.00/3600) × 15 = 23.785° × 15 = **356.775°**

例（ALeRCE 示例）：`12 11 11.11 +15 14 14.3`
- RA = (12 + 11/60 + 11.11/3600) × 15 = **182.79629166666666**
- Dec = 15 + 14/60 + 14.3/3600 = **15.237305555555555**

### 十进制度 → 时角式

- RA：`度/15` → 时；余 ×60 → 分；再由余 ×60 → 秒。
- Dec：整数部分为度，余 ×60 → 分，再余 ×60 → 秒（负号留在最外层）。

### 自查

- RA ∈ [0,360) 度（或 [0,24) h），Dec ∈ [−90, +90]；
- 换回去误差应在角秒级；给坐标时写清来源（用户输入 / 观测记录）。

## 2. 链接模板

| 用途 | 模板 | 备注 |
|---|---|---|
| TNS 检索（半径 10 arcsec） | `https://www.wis-tns.org/search?ra={RA}&decl={DEC}&radius=10&coords_unit=arcsec&include_frb=1` | 只给链接，程序不代查（反机器人） |
| ALeRCE / ZTF | `https://alerce.online/?ranking=1&ra={RA_deg}&dec={DEC_deg}&radius=50&count=false&page=1&perPage=20&sortBy=probability&sortDesc=true` | RA/DEC 用**十进制度**；只给链接 |
| CDS 星图 | `https://portal.cds.unistra.fr/?target={RA}%20{DEC}` | 时角式，空格用 `%20` |
| VSX 检索页 | `https://vsx.aavso.org/index.php?view=search.top` | Position 填坐标、格式 Sexagesimal、Size 10 arc minutes |
| VSX 详情页 | `https://vsx.aavso.org/index.php?view=detail.top&oid={OID}` | OID 必须是**真实数字**；拿不到就只给检索页 |
| VSX 按名核对 | `https://vsx.aavso.org/index.php?view=api.object&ident={名称}` | 名称 URL 编码 |
| Rochester 新星表 | `https://www.rochesterastronomy.org/novae.html` | **10 角秒内算命中**，需自行比对 |
| FTS 在线解析 | `https://sunguoyou.lamost.org/fit.html` | 上传/拖入即可 |

## 3. URL 编码

| 字符 | 编码 |
|---|---|
| `:` | `%3A` |
| `+` | `%2B` |
| 空格 | `%20` |
| `-` | 不用编码（但坐标里的负号要保留为 `-`） |

## 4. 硬规矩（踩过的坑）

1. **只允许 `vsx.aavso.org`**：`www.aavso.org/vsx/...` 实测 403，禁止输出。
2. **`oid` 必须真实数字**：禁止 `oid=`、`oid=...`、`oid=<oid>` 这类占位写法。
3. **坐标检索拼不出 URL**：VSX 的坐标检索是 POST 表单 → 给检索页并说明「把坐标粘到
   Position、选 Sexagesimal、Size 10 arc minutes」。
4. **TNS / ALeRCE 只给链接**，明确写「请点开自行查询」。
5. 链接发出前自检一遍：域名对、参数全、没有占位符、RA/DEC 是换算后的真实值。
