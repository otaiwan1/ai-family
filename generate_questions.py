import requests
import json
import concurrent.futures
import time
import re
import os

OLLAMA_URL = "http://127.0.0.1:6728/api/generate"
MODEL = "gemma4:31b"
NUM_REQUESTS_PER_QUESTION = 100
OUTPUT_FILE = "questions_db.json"

# 讀取 .env 中的 API Key
GEMINI_API_KEY = ""
if os.path.exists(".env"):
    with open(".env", "r") as f:
        for line in f:
            if line.startswith("GEMINI_API_KEY="):
                GEMINI_API_KEY = line.strip().split("=", 1)[1].strip()

questions = [
    "說到工程師最常喝的飲料，你會想到什麼？",
    "程式出 Bug 時，第一時間會脫口而出說什麼？",
    "除了寫程式，資工系學生最常在哪裡花時間？",
    "寫程式最不想碰到的程式碼語言是哪一個？",
    "在鍵盤上最常壞掉的按鍵是哪幾個字元？"
] # 我們會保留舊的 questions list，這邊只是佔位符，實際會把原本的抓出來

def get_single_answer(question):
    prompt_text = f"你現在是一位隨機被抽中街訪的路人，在合理的前提下，可以有多一點的創意，就像是世界上各種不同的人被問到一樣。\n請簡短回答以下問題（只需回答一個名詞或極簡短詞語，不要有任何解釋或多餘的文字）：\n{question}"
    payload = {
        "model": MODEL,
        "prompt": prompt_text,
        "stream": False,
        "options": {
            "temperature": 1.5,
            "top_p": 0.95
        }
    }
    try:
        response = requests.post(OLLAMA_URL, json=payload, timeout=180)
        response.raise_for_status()
        return response.json().get("response", "").strip()
    except Exception as e:
        print(f"Error querying AI: {e}")
        return None

def normalize_answers(question, raw_answers):
    answers_text = ", ".join([a for a in raw_answers if a])
    prompt_text = f"我問了 100 個 AI 以下問題：「{question}」。\n這是他們的回覆清單：\n{answers_text}\n\n請幫我將意思相似或相同的答案合併（例如「蘋果」、「紅蘋果」、「Apple」合併為「蘋果」）。\n然後統計每個答案出現的次數，並列出出現次數最高的 Top 5 答案。\n\n務必遵守以下規範：\n1. 請**只**回傳 JSON 格式的陣列。\n2. 絕對不要有其他 Markdown 標記 (如 ```json ...) 或是其他說明文字。\n3. 欄位只能包含 \"answer\" 和 \"count\"。"
    
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
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
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
    print("=== AI Family Feud 題庫生成腳本開始 ===")
    
    if not GEMINI_API_KEY:
        print("警告: 找不到 .env 檔案或 GEMINI_API_KEY 未設定，Gemini 正規化將會失敗。")
        
    # read problem from questions.txt here!
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
