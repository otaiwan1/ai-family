# AI Family Feud (AI 版家庭大哉問)

## 遊戲核心概念
傳統的 Family Feud（家庭大哉問）是建立在「我們訪問了 100 個人」的基礎上。而這個 AI 版本的概念是：**「我們詢問了 AI 100 次」**。
玩家需要猜測大語言模型（LLM）在被問到同一個問題多次時，最常給出的答案是什麼。

## 遊戲規則 (草案)

### 1. 題目生成與底層機制
- 系統準備一系列開放式的趣味問題（例如：「請說出一種紅色的水果」、「如果要在荒島度過餘生，必須帶的一樣物品是什麼？」）。
- 在遊戲準備階段（或事先預先生成），系統會將同一個問題，搭配一定的隨機性（調整 Temperature），向 **Google Gemma 4 (31B)** 模型詢問 $N$ 次（例如 50 或 100 次）。
- 系統會自動統計 AI 的回覆，將相似或同義的答案合併（例如「蘋果」和「Apple」與「一顆蘋果」算同一個），並化為排行榜。取出現頻率最高的 Top 5 ~ Top 8 作為本題的「上榜答案」。

### 2. 遊玩流程
1. **開場**：主持人或系統亮出題目，告訴玩家這題共有多少個 AI 答案在榜上。
2. **搶答/輪流回答**：
   - 玩家或兩支隊伍輪流猜測 AI 最常回答的事物。
   - 如果猜中榜上的答案，該答案就會翻開，玩家獲得對應的「AI 回答次數」作為分數。
   - 如果猜錯（AI 從來沒這樣回答過，或是不在榜單上），就會得到一個「❌ (Strike)」。
3. **出局機制**：當一方累積 3 個「❌」時，另一方有機會進行「搶分（Steal）」。若搶分方猜中剩下未翻開的任何一個答案，則整局累計的分數歸搶分方所有；若猜錯，則分數歸原隊伍所有。
4. **結算**：公布該題所有 AI 的答案，積分最高的隊伍獲勝。

## 為什麼好玩？
- **探索 AI 的偏見與常理**：玩家除了要用人類常理思考，還要試著揣摩「AI 的腦迴路」，這可能會產生令人意外、好笑的答案分佈。
- **結合隨機性**：有些 AI 答案可能非常荒謬，這將作為該遊戲的「彩蛋」。

## 題庫生成

目前題庫生成統一使用 OpenRouter。請在專案根目錄的 `.env` 放入：

```bash
OPENROUTER_API_KEY=你的_key
```

模型與每題詢問次數寫在 `openrouter_models.json`。預設每題共問 100 次，分散到：

- `openai/gpt-5.5`: 17 次
- `deepseek/deepseek-v4-flash`: 17 次
- `minimax/minimax-m3`: 17 次
- `anthropic/claude-sonnet-4.6`: 17 次
- `google/gemini-3.5-flash`: 16 次
- `qwen/qwen3.7-plus`: 16 次

先檢查模型是否存在：

```bash
python generate_questions.py --validate-models
```

開始或續跑題庫：

```bash
python generate_questions.py
```

預設速度設定在 `openrouter_models.json` 的 `defaults`：

- `concurrency`: 同時執行的 worker 數，預設 8。
- `requests_per_minute`: 本機端主動節流，預設 60 requests/minute。

也可以臨時用命令列覆蓋：

```bash
python generate_questions.py --concurrency 12 --rpm 120
```

OpenRouter 若回傳 429，腳本會依照 `Retry-After` 等待後重試。若使用 `:free` 模型，OpenRouter 官方限制目前是 20 requests/minute，且每日額度會受帳戶購買 credits 影響；付費模型的實際限制由 OpenRouter 與上游 provider 管理。

生成過程每取得一筆答案就會重新讀取 `questions_db.json` 並只更新當前題目，所以你可以同時手動修改其他已完成題目的答案或分數，不會被腳本用舊快取覆蓋。

## 啟動 Web 遊戲

正式使用只需要一個 port。腳本會先 build React 前端，再由 FastAPI 在同一個 port 提供前端、`/api/*` 與 Socket.IO。

```bash
PORT=8000 ./start_web.sh
```

開啟：

- `http://localhost:8000/host`
- `http://localhost:8000/audience`
- `http://localhost:8000/admin`

## Admin Panel

`/admin` 可以直接管理 `questions_db.json`：

- 修改題目、上榜答案、排序與分數分布。
- 新增或刪除題目；每次成功修改前都會自動備份至 `backups/`。
- 搜尋題目與答案、查看 raw answers 和各模型回答數。
- 匯出目前的 `questions_db.json`。
- 在背景補齊 AI 回答，可選擇重新計算分布或先清空舊回答；生成期間仍可編輯其他題目。

Host、Audience 與 Admin Panel 都需要先輸入存取密碼。預設密碼是 `AI2026`，可在 `.env` 覆寫：

```bash
ACCESS_PASSWORD=請換成一段足夠長的存取密碼
```

登入成功後 token 只保存在目前分頁的 session storage；服務重新啟動後需要重新登入。
