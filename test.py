import requests
import time
import concurrent.futures

TARGET_MODEL = "gemma4:e2b" # 請替換成你實際使用的模型
API_URL = "http://localhost:6728/api/generate"
n = 2
# 準備三個稍微不同的 Prompt，避免快取干擾
PROMPTS = ["你是個受到街訪的受試者，請隨機(模仿社會大眾)回答 以下問題，只要簡單的輸出一個名詞或簡短答案即可，不用多做任何說明: 下雨天最適合做的一個運動是什麼?"] * n

def send_request(task_id, prompt):
    print(f"[{task_id}] 🚀 請求已發送，等待生成中...")
    
    start_time = time.time()
    
    data = {
        "model": TARGET_MODEL,
        "prompt": prompt,
        "stream": False
    }
    
    try:
        response = requests.post(API_URL, json=data)
        response.raise_for_status()
        result = response.json()
        
        eval_count = result.get("eval_count", 0)
        eval_duration_s = result.get("eval_duration", 0) / 1e9
        
        tps = eval_count / eval_duration_s if eval_duration_s > 0 else 0
        total_time = time.time() - start_time
        
        return f"[{task_id}] ✅ 完成! 總等待: {total_time:.2f}s | 生成耗時: {eval_duration_s:.2f}s | 速度: {tps:.2f} TPS | Tokens: {eval_count}"
        
    except Exception as e:
        return f"[{task_id}] ❌ 請求失敗: {e}"

def main():
    print(f"開始針對 {TARGET_MODEL} 進行併發測試 (總共 {len(PROMPTS)} 個請求)...\n")
    
    overall_start_time = time.time()
    
    # 使用 ThreadPoolExecutor 來同時發出請求
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(PROMPTS)) as executor:
        # 將任務提交給執行緒池
        futures = {executor.submit(send_request, f"任務-{i+1}", prompt): i for i, prompt in enumerate(PROMPTS)}
        
        # 接收完成的結果
        for future in concurrent.futures.as_completed(futures):
            print(future.result())
            
    overall_time = time.time() - overall_start_time
    print("-" * 50)
    print(f"🏁 所有請求執行完畢！腳本總運行時間: {overall_time:.2f} 秒")

if __name__ == "__main__":
    main()