# 內含的第三方元件（skl 收錄時補上）

`scripts/am.mjs` 是原作者用 esbuild 打包的單一檔案，裡面含：

| 元件 | 版本（依原 repo） | 授權 | 授權全文 |
|---|---|---|---|
| marked | 18.0.14 | MIT | `marked-LICENSE.md`（取自 github.com/markedjs/marked） |
| @dagrejs/dagre | 3.1.1 | MIT | `dagre-LICENSE.txt`（取自 github.com/dagrejs/dagre）；打包檔裡指向的 dagre.esm.js.LEGAL.txt 原作者沒附 |
| Temml | 0.14.0 | MIT，另含 Apache-2.0 部分（mhchem、React 的 hyphenate／escape） | 寫在 `scripts/am.mjs` 檔尾的授權註解 |
| Bangers 字型 | — | SIL OFL 1.1 | `OFL-Bangers.txt` |

本技能其餘部分：MIT，見上一層的 `LICENSE`。
