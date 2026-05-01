import requests
import json
import concurrent.futures
import time
import re
import os

# OpenAI Compatible API 設定
API_URL = "http://127.0.0.1:6728/v1/chat/completions" # 根據您的 Llama 伺服器修改 IP 或 port
MODEL = "qwen3.5:122b" # 根據您的模型名稱修改
NUM_REQUESTS_PER_QUESTION = 100
OUTPUT_FILE = "questions_db.json"

# 讀取 .env 中的 API Key (用於 Gemini 正規化)
GEMINI_API_KEY = ""
OPENAI_API_KEY = "sk-xxxxxxxx" # 如果您的 Local Server 需要 API Key，可在此修改

if os.path.exists(".env"):
    with open(".env", "r") as f:
        for line in f:
            if line.startswith("GEMINI_API_KEY="):
                GEMINI_API_KEY = line.strip().split("=", 1)[1].strip()
            # 如果 .env 裡面有存 OPENAI 的 KEY 也可以一併讀取
            elif line.startswith("OPENAI_API_KEY="):
                OPENAI_API_KEY = line.strip().split("=", 1)[1].strip()


def get_single_answer(question):
    # 1. 將設定與格式限制移至 System Prompt
    system_prompt = """你現在參與一場街頭隨機問卷調查。
    請發揮極大的創意與想像力，給出符合人類直覺但具備多樣性的答案。
    【嚴格輸出限制】
    1. 僅能輸出「一個名詞」或「一個極短的動詞片語」（1~8個字內）。
    2. 絕對禁止任何標點符號、解釋、問候語或換行。
    正確輸出範例：重開機
    錯誤輸出範例：我會選擇重開機，因為..."""

    # 2. User Prompt 保持極致乾淨，只負責傳遞題目
    user_prompt = f"問題：{question}"
        
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        # 參數優化區
        "temperature": 1.4,        # 提高隨機性，但避開語意崩壞的臨界點
        "top_p": 0.90,             # 稍微收緊候選池，確保產出的詞彙具備合理性
        "presence_penalty": 0.6,   # (選用) 懲罰模型產出過於常見的詞彙，激發創意
        "stream": False
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {OPENAI_API_KEY}"
    }
    
    try:
        response = requests.post(API_URL, headers=headers, json=payload, timeout=6000)
        response.raise_for_status()
        # 解析 OpenAI 格式的回傳值
        return response.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"Error querying AI: {e}")
        return None

def normalize_answers(question, raw_answers):
    answers_text = ", ".join([a for a in raw_answers if a])
    prompt_text = f"我問了 100 個 AI 以下問題：「{question}」。\n這是他們的回覆清單：\n{answers_text}\n\n請幫我將意思相似或相同的答案合併（例如「蘋果」、「紅蘋果」、「Apple」合併為「蘋果」）。\n然後統計每個答案出現的次數，並列出出現次數最高的 Top 4-8 答案。\n\n務必遵守以下規範：\n1. 請**只**回傳 JSON 格式的陣列。\n2. 絕對不要有其他 Markdown 標記 (如 ```json ...) 或是其他說明文字。\n3. 欄位只能包含 \"answer\" 和 \"count\"。"
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite-preview:generateContent?key={GEMINI_API_KEY}"
    for attempt in range(3):
        payload = {
            "contents": [{
                "parts": [{"text": prompt_text}]
            }],
            "generationConfig": {
                "responseMimeType": "application/json"
            }
        }
        try:
            response = requests.post(url, json=payload, timeout=60)
            response.raise_for_status()
            result_text = response.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            
            match = re.search(r"\[.*\]", result_text, re.DOTALL)
            if match:
                result_text = match.group(0)
            return json.loads(result_text)
        except json.JSONDecodeError as e:
            print(f"  -> [{question}] Gemini JSON 解析失敗 (第 {attempt+1}/3 次嘗試): {e}")
            print(f"========== Gemini 原始分析回傳內容 ==========\n{result_text}\n=====================================")
            time.sleep(2)
        except Exception as e:
            print(f"  -> [{question}] Gemini 請求錯誤 (第 {attempt+1}/3 次嘗試): {e}")
            time.sleep(2)
    return []

def safe_save_db(db):
    tmp_file = OUTPUT_FILE + ".tmp"
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(list(db.values()), f, ensure_ascii=False, indent=4)
    os.replace(tmp_file, OUTPUT_FILE)

def process_question(question, db_entry, db_map):
    print(f"\n=============================================")
    raw_answers = db_entry.get("raw_answers", [])
    needed = NUM_REQUESTS_PER_QUESTION - len(raw_answers)
    
    if needed > 0:
        print(f"[{question}] 準備補齊剩餘的 {needed} 次回答 (已有 {len(raw_answers)} 次)...")
        # 因應 llama.cpp 搭配 72 執行緒的硬體配置，大幅提高並發數量
        max_threads = min(4, needed)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_threads) as executor:
            futures = {executor.submit(get_single_answer, question): i for i in range(needed)}
            count_since_save = 0
            for future in concurrent.futures.as_completed(futures):
                ans = future.result()
                if ans:
                    raw_answers.append(ans)
                    db_entry["raw_answers"] = raw_answers
                    db_entry["raw_answers_count"] = len(raw_answers)
                    count_since_save += 1
                    
                    # 每收到 5 個回答就寫入一次資料庫，或者湊齊時寫入
                    if count_since_save >= 5 or len(raw_answers) == NUM_REQUESTS_PER_QUESTION:
                        safe_save_db(db_map)
                        count_since_save = 0
                        print(f"  -> [{question}] 已自動存檔進度: {len(raw_answers)}/{NUM_REQUESTS_PER_QUESTION}")
                        
    # 檢查是否需要進行正規化 (如果有足夠的答案且還沒正規化過)
    if "top_answers" not in db_entry or not db_entry["top_answers"]:
        print(f"[{question}] 收集到 {len(raw_answers)} 個有效回答，呼叫 Gemini 進行正規化...")
        normalized_results = normalize_answers(question, raw_answers)
        db_entry["top_answers"] = normalized_results
        safe_save_db(db_map)
        print(f"[{question}] 正規化完成並已存入資料庫！")
    else:
        print(f"[{question}] 本題已正規化過，跳過此步驟。")

def main():
    print("=== AI Family Feud 題庫生成腳本開始 (OpenAI API 版) ===")
    
    if not GEMINI_API_KEY:
        print("警告: 找不到 .env 檔案或 GEMINI_API_KEY 未設定，Gemini 正規化將會失敗。")
        
    global questions
    questions = []
    if os.path.exists("questions.txt"):
        with open("questions.txt", "r", encoding="utf-8") as f:
            questions = [line.strip() for line in f if line.strip()]
    else:
        print("警告: 找不到 questions.txt，將使用預設備用題庫。")

    print(f"總共載入了 {len(questions)} 道題目")
    
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
