import json
import os
import asyncio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import socketio

# 建立 FastAPI app 與 Socket.IO server
sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins='*')
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

socket_app = socketio.ASGIApp(sio, app)

# 遊戲狀態定義
game_state = {
    "questions": [],           # 從 questions_db.json 讀取的完整題庫
    "current_question_idx": 0, # 目前在第幾題
    "revealed_answers": [],    # 目前這題翻開了哪些答案的 index
    "strikes": 0,              # 目前得幾個叉叉 (0-3)
    "team_a_score": 0,
    "team_b_score": 0,
    "current_pool": 0,         # 獎金池，答錯或換題時歸零 / 結算時加到隊伍
}

def load_database():
    db_path = os.path.join(os.path.dirname(__file__), "..", "questions_db.json")
    if os.path.exists(db_path):
        try:
            with open(db_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    data = json.loads(content)
                    # 篩選掉還沒 normalize 完的資料
                    game_state["questions"] = [q for q in data if q.get("top_answers")]
                    print(f"[系統] 成功載入 {len(game_state['questions'])} 題。")
        except Exception as e:
            print(f"[錯誤] 讀取題庫失敗: {e}")
    else:
         print(f"[警告] 找不到題庫檔案 {db_path}，請確認是否已生成。")

# --- Socket Event Handlers ---
@sio.event
async def connect(sid, environ):
    print(f"Client connected: {sid}")
    # 有人連線時，先將目前的遊戲狀態送給他
    await sio.emit("state_update", _get_sanitized_state(), to=sid)
    # 若他是 Host，也可以再送完整包含答案的 state 給他 (此處我們統一在前端靠 boolean 畫面過濾，實際上為了防作弊可以分開 API)
    await sio.emit("host_state_update", game_state, to=sid)

@sio.event
async def disconnect(sid):
    print(f"Client disconnected: {sid}")

# --- 主持人控制指令 ---
@sio.event
async def next_question(sid):
    if game_state["current_question_idx"] < len(game_state["questions"]) - 1:
        game_state["current_question_idx"] += 1
        _reset_round_state()
        await _broadcast_state()

@sio.event
async def prev_question(sid):
    if game_state["current_question_idx"] > 0:
        game_state["current_question_idx"] -= 1
        _reset_round_state()
        await _broadcast_state()

@sio.event
async def reveal_answer(sid, answer_idx):
    if answer_idx not in game_state["revealed_answers"]:
        game_state["revealed_answers"].append(answer_idx)
        # 加上該題目的分數
        curr_q = game_state["questions"][game_state["current_question_idx"]]
        top_answers = curr_q.get("top_answers", [])
        if answer_idx < len(top_answers):
            game_state["current_pool"] += top_answers[answer_idx].get("count", 0)
        
        await _broadcast_state()
        await sio.emit("play_sound", "ding") # 告訴前端放叮咚聲

@sio.event
async def show_strike(sid):
    if game_state["strikes"] < 3:
        game_state["strikes"] += 1
    # 無論如何都會播放音效跟動畫
    await _broadcast_state()
    await sio.emit("play_sound", "strike")

@sio.event
async def clear_strikes(sid):
    game_state["strikes"] = 0
    await _broadcast_state()

@sio.event
async def award_points(sid, data):
    # data: "team_a" or "team_b"
    team = data.get("team")
    if team == "team_a":
        game_state["team_a_score"] += game_state["current_pool"]
    elif team == "team_b":
        game_state["team_b_score"] += game_state["current_pool"]
    
    game_state["current_pool"] = 0
    await _broadcast_state()

@sio.event
async def modify_score(sid, data):
    team = data.get("team")
    amount = data.get("amount", 0)
    if team == "team_a":
        game_state["team_a_score"] += amount
    elif team == "team_b":
        game_state["team_b_score"] += amount
    await _broadcast_state()

@sio.event
async def reload_database(sid):
    load_database()
    await _broadcast_state()

@sio.event
async def reset_game(sid):
    game_state["current_question_idx"] = 0
    game_state["team_a_score"] = 0
    game_state["team_b_score"] = 0
    _reset_round_state()
    await _broadcast_state()

# --- 輔助函數 ---
def _reset_round_state():
    game_state["revealed_answers"] = []
    game_state["strikes"] = 0
    game_state["current_pool"] = 0

def _get_sanitized_state():
    # 傳給「觀眾畫面」的狀態，把還沒翻開的文字隱藏掉，並且不傳 raw_answers，避免作弊與減少頻寬
    safe_state = json.loads(json.dumps(game_state)) # deep copy
    
    if safe_state["questions"]:
        curr_q = safe_state["questions"][safe_state["current_question_idx"]]
        safe_answers = []
        for i, ans in enumerate(curr_q.get("top_answers", [])):
            if i in safe_state["revealed_answers"]:
                safe_answers.append(ans)
            else:
                safe_answers.append({"answer": "???", "count": 0})
        
        # 只保留當前題目，其餘不送
        safe_state["current_question_data"] = {
            "question": curr_q.get("question"),
            "answers": safe_answers
        }
    else:
        safe_state["current_question_data"] = None
        
    # 清除 questions 的肥大資料
    del safe_state["questions"]
    return safe_state

async def _broadcast_state():
    # 觀眾用的過濾版
    await sio.emit("state_update", _get_sanitized_state())
    # 主持人用的詳盡版
    await sio.emit("host_state_update", game_state)


@app.on_event("startup")
async def startup_event():
    load_database()

if __name__ == "__main__":
    import uvicorn
    # 本地開發測試可以 run 這個檔，會在 8000 port
    uvicorn.run("app:socket_app", host="0.0.0.0", port=8000, reload=True)
