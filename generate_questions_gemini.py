import requests
import json
import time
import re
import os

NUM_REQUESTS_PER_QUESTION = 100
OUTPUT_FILE = "questions_db.json"

# 讀取 .env 中的 API Key
GEMINI_API_KEY = ""

if os.path.exists(".env"):
    with open(".env", "r") as f:
        for line in f:
            if line.startswith("GEMINI_API_KEY="):
                GEMINI_API_KEY = line.strip().split("=", 1)[1].strip()

def make_gemini_request(payload):
    """統整 API 請求處理，包含錯誤重試與每日限速等待"""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite-preview:generateContent?key={GEMINI_API_KEY}"
    
    while True:
        try:
            response = requests.post(url, json=payload, timeout=60)
            
            # 處理 429 Rate Limit
            if response.status_code == 429:
                print("  -> 達到 API 速率限制 (429)，等待 600 秒後重試...")
                time.sleep(600)
                continue
                
            response.raise_for_status()
            
            # 成功取得回應後，固定等待 180 秒
            # 一天有 86400 秒，86400 / 480 = 180，這樣一天最多只會發出 480 個 request
            print("  -> 等待 180 秒以符合每天 480 次的限制...")
            time.sleep(180)
            
            return response.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            
        except requests.exceptions.RequestException as e:
            print(f"  -> API 網路請求錯誤: {e}，等待 60 秒後重試...")
            time.sleep(60)
        except (KeyError, IndexError) as e:
            print(f"  -> 解析 API 回應失敗: {e}，等待 60 秒後重試...")
            time.sleep(60)

def get_single_answer(question):
    system_prompt = """你現在參與一場街頭隨機問卷調查。
    請發揮極大的創意與想像力，給出符合人類直覺但具備多樣性的答案。
    【嚴格輸出限制】
    1. 僅能輸出「一個名詞」或「一個極短的動詞片語」（1~8個字內）。
    2. 絕對禁止任何標點符號、解釋、問候語或換行。
    正確輸出範例：重開機
    錯誤輸出範例：我會選擇重開機，因為..."""

    user_prompt = f"問題：{question}"
    
    payload = {
        "systemInstruction": {
            "parts": [{"text": system_prompt}]
        },
        "contents": [{
            "parts": [{"text": user_prompt}]
        }],
        "generationConfig": {
            "temperature": 1.4,
            "topP": 0.90,
            "presencePenalty": 0.6
        }
    }
    
    return make_gemini_request(payload)

def normalize_answers(question, raw_answers):
    answers_text = ", ".join([a for a in raw_answers if a])
    prompt_text = f"我問了 100 個 AI 以下問題：「{question}」。\n這是他們的回覆清單：\n{answers_text}\n\n請幫我將意思相似或相同的答案合併（例如「蘋果」、「紅蘋果」、「Apple」合併為「蘋果」）。\n然後統計每個答案出現的次數，並列出出現次數最高的 Top 4-8 答案。\n\n務必遵守以下規範：\n1. 請**只**回傳 JSON 格式的陣列。\n2. 絕對不要有其他 Markdown 標記 (如 ```json ...) 或是其他說明文字。\n3. 欄位只能包含 \"answer\" 和 \"count\"。"
    
    payload = {
        "contents": [{
            "parts": [{"text": prompt_text}]
        }],
        "generationConfig": {
            "responseMimeType": "application/json"
        }
    }
    
    while True:
        result_text = make_gemini_request(payload)
        
        try:
            match = re.search(r"\[.*\]", result_text, re.DOTALL)
            if match:
                result_text = match.group(0)
            return json.loads(result_text)
        except json.JSONDecodeError as e:
            print(f"  -> [{question}] Gemini JSON 解析失敗: {e}，重新嘗試正規化...")
            time.sleep(5) # 稍微喘息再重試，下一次 make_gemini_request 也會自動限速

def safe_save_db(db):
    tmp_file = OUTPUT_FILE + ".tmp"
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(list(db.values()), f, ensure_ascii=False, indent=4)
    os.replace(tmp_file, OUTPUT_FILE)

def process_question(question, db_entry, db_map):
    print(f"\n=============================================")
    raw_answers = db_entry.get("raw_answers", [])
    
    # 確保收集滿 NUM_REQUESTS_PER_QUESTION 才停止
    while len(raw_answers) < NUM_REQUESTS_PER_QUESTION:
        needed = NUM_REQUESTS_PER_QUESTION - len(raw_answers)
        print(f"[{question}] 準備補齊剩餘的 {needed} 次回答 (已有 {len(raw_answers)} 次)...")
        
        ans = get_single_answer(question)
        if ans:
            raw_answers.append(ans)
            db_entry["raw_answers"] = raw_answers
            db_entry["raw_answers_count"] = len(raw_answers)
            
            # 每收到 5 個回答就寫入一次資料庫
            if len(raw_answers) % 5 == 0 or len(raw_answers) == NUM_REQUESTS_PER_QUESTION:
                safe_save_db(db_map)
                print(f"  -> [{question}] 已存檔進度: {len(raw_answers)}/{NUM_REQUESTS_PER_QUESTION}")
                        
    # 檢查是否需要進行正規化 (確保答案足夠且還沒正規化過)
    if len(raw_answers) >= NUM_REQUESTS_PER_QUESTION and (not db_entry.get("top_answers")):
        print(f"[{question}] 已收集滿 {len(raw_answers)} 個回答，呼叫 Gemini 進行正規化...")
        normalized_results = normalize_answers(question, raw_answers)
        db_entry["top_answers"] = normalized_results
        safe_save_db(db_map)
        print(f"[{question}] 正規化完成並已存入資料庫！")
    elif db_entry.get("top_answers"):
        print(f"[{question}] 本題已正規化過，跳過此步驟。")

def main():
    print("=== AI Family Feud 題庫生成腳本開始 (純 Gemini API 版) ===")
    
    if not GEMINI_API_KEY:
        print("錯誤: 找不到 .env 檔案或 GEMINI_API_KEY 未設定，無法執行。")
        return
        
    global questions
    questions = []
    if os.path.exists("questions.txt"):
        with open("questions.txt", "r", encoding="utf-8") as f:
            questions = [line.strip() for line in f if line.strip()]
    else:
        print("警告: 找不到 questions.txt，將使用預設備用題庫。")

    print(f"總共載入了 {len(questions)} 道題目")
    print(f"【注意】已啟用無限期自動等待機制，每天最多 480 次請求 (間隔 180 秒)。可以放著讓它一直跑不中斷！")
    
    db_map = {}
    if os.path.exists(OUTPUT_FILE):
        try:
            with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    db_list = json.loads(content)
                    db_map = {item["question"]: item for item in db_list}
        except Exception as e:
            print(f"讀取舊 DB 失敗: {e}")
            
    print(f"已完成或部分完成 {len(db_map)} 題。")
    print("=============================================\n")
    
    for q in questions:
        if q not in db_map:
            db_map[q] = {"question": q, "raw_answers": [], "raw_answers_count": 0}
            
        entry = db_map[q]
        if "top_answers" in entry and entry["top_answers"]:
            print(f"[{q}] 已經在題庫中且歸納完成，跳過。")
            continue
            
        process_question(q, entry, db_map)

    print("\n✅ 所有題庫生成完畢！")

if __name__ == "__main__":
    main()
