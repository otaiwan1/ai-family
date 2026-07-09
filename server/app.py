import asyncio
import json
import os
import random
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import socketio
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

ROOT_DIR = Path(__file__).resolve().parent.parent
CLIENT_DIST_DIR = ROOT_DIR / "client" / "dist"
DB_PATH = Path(os.environ.get("QUESTIONS_DB_PATH", ROOT_DIR / "questions_db.json")).resolve()
MODELS_CONFIG_PATH = Path(os.environ.get("OPENROUTER_MODELS_PATH", ROOT_DIR / "openrouter_models.json")).resolve()
BACKUP_DIR = Path(os.environ.get("QUESTIONS_BACKUP_DIR", ROOT_DIR / "backups")).resolve()

import sys

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from generate_questions import (  # noqa: E402
    RateLimiter,
    atomic_write_json,
    get_question_entry,
    load_db,
    load_env_file,
    mutate_db,
    normalize_answers,
    process_question,
    read_json,
    replace_top_answers,
)

load_env_file(ROOT_DIR / ".env")

sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*")
app = FastAPI(title="AI Family Feud")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

socket_app = socketio.ASGIApp(sio, app)

game_state = {
    "questions": [],
    "current_question_idx": 0,
    "revealed_answers": [],
    "strikes": 0,
    "team_a_score": 0,
    "team_b_score": 0,
    "current_pool": 0,
}

generation_job: dict[str, Any] = {
    "status": "idle",
    "current": None,
    "queue": [],
    "completed_jobs": [],
    "failed_jobs": [],
    "question": None,
    "started_at": None,
    "finished_at": None,
    "error": None,
    "raw_answers_count": None,
    "target_count": None,
}
generation_worker_task: asyncio.Task | None = None
generation_queue_lock = asyncio.Lock()
active_sessions: set[str] = set()


class LoginPayload(BaseModel):
    password: str = Field(min_length=1, max_length=300)


class AnswerPayload(BaseModel):
    answer: str = Field(min_length=1, max_length=80)
    count: int = Field(ge=0, le=100000)


class QuestionCreatePayload(BaseModel):
    question: str = Field(min_length=1, max_length=300)
    top_answers: list[AnswerPayload] = Field(default_factory=list)


class QuestionUpdatePayload(QuestionCreatePayload):
    expected_question: str | None = None


class FillAnswersPayload(BaseModel):
    target_count: int = Field(default=100, ge=1, le=1000)
    renormalize: bool = True
    reset_existing: bool = False
    concurrency: int | None = Field(default=None, ge=1, le=64)
    requests_per_minute: float | None = Field(default=None, ge=0, le=3000)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_valid_session(token: str | None) -> bool:
    return bool(token and token in active_sessions)


def verify_access(x_access_token: str | None = Header(default=None)) -> str:
    if not is_valid_session(x_access_token):
        raise HTTPException(status_code=401, detail="Authentication required")
    return x_access_token


def serialize_answers(answers: list[AnswerPayload]) -> list[dict[str, Any]]:
    return [{"answer": item.answer.strip(), "count": item.count} for item in answers]


def create_backup(db: list[dict[str, Any]], reason: str) -> str:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup_path = BACKUP_DIR / f"questions_db-{stamp}-{reason}.json"
    atomic_write_json(backup_path, db)
    return str(backup_path)


def mutate_admin_db(reason: str, updater):
    def locked_update(db: list[dict[str, Any]]):
        original_db = json.loads(json.dumps(db))
        result = updater(db)
        backup_path = create_backup(original_db, reason)
        return result, backup_path

    return mutate_db(DB_PATH, locked_update)


def validate_index(db: list[dict[str, Any]], index: int) -> dict[str, Any]:
    if index < 0 or index >= len(db):
        raise HTTPException(status_code=404, detail="Question not found")
    return db[index]


def ensure_question_not_running(question: str) -> None:
    current = generation_job.get("current") or {}
    if current.get("status") == "running" and current.get("question") == question:
        raise HTTPException(status_code=409, detail="This question is currently being generated")


def load_database() -> None:
    try:
        data = load_db(DB_PATH)
        game_state["questions"] = [question for question in data if question.get("top_answers")]
        if game_state["questions"]:
            game_state["current_question_idx"] = min(
                game_state["current_question_idx"], len(game_state["questions"]) - 1
            )
        else:
            game_state["current_question_idx"] = 0
        print(f"[系統] 成功載入 {len(game_state['questions'])} 題。")
    except Exception as exc:
        game_state["questions"] = []
        print(f"[錯誤] 讀取題庫失敗: {exc}")


def question_summary(index: int, question: dict[str, Any]) -> dict[str, Any]:
    return {
        "index": index,
        "question": question.get("question", ""),
        "top_answers": question.get("top_answers") or [],
        "raw_answers_count": len(question.get("raw_answers") or []),
        "model_counts": question.get("model_counts") or {},
        "updated_at": question.get("updated_at"),
    }


@sio.event
async def connect(sid, environ, auth):
    token = (auth or {}).get("token") if isinstance(auth, dict) else None
    if not is_valid_session(token):
        raise ConnectionRefusedError("Authentication required")
    print(f"Client connected: {sid}")
    await sio.emit("state_update", _get_sanitized_state(), to=sid)
    await sio.emit("host_state_update", game_state, to=sid)


@sio.event
async def disconnect(sid):
    print(f"Client disconnected: {sid}")


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
    if not game_state["questions"] or not isinstance(answer_idx, int):
        return
    current = game_state["questions"][game_state["current_question_idx"]]
    answers = current.get("top_answers", [])
    if answer_idx < 0 or answer_idx >= len(answers) or answer_idx in game_state["revealed_answers"]:
        return
    game_state["revealed_answers"].append(answer_idx)
    game_state["current_pool"] += int(answers[answer_idx].get("count", 0))
    await _broadcast_state()
    await sio.emit("play_sound", "ding")


@sio.event
async def show_strike(sid):
    game_state["strikes"] = min(3, game_state["strikes"] + 1)
    await _broadcast_state()
    await sio.emit("strike_overlay", {"count": game_state["strikes"]})
    await sio.emit("play_sound", "strike")


@sio.event
async def show_single_strike(sid):
    await sio.emit("strike_overlay", {"count": 1})
    await sio.emit("play_sound", "strike")


@sio.event
async def clear_strikes(sid):
    game_state["strikes"] = 0
    await _broadcast_state()


@sio.event
async def award_points(sid, data):
    team = (data or {}).get("team")
    if team == "team_a":
        game_state["team_a_score"] += game_state["current_pool"]
    elif team == "team_b":
        game_state["team_b_score"] += game_state["current_pool"]
    game_state["current_pool"] = 0
    await _broadcast_state()


@sio.event
async def modify_score(sid, data):
    team = (data or {}).get("team")
    amount = int((data or {}).get("amount", 0))
    if team == "team_a":
        game_state["team_a_score"] += amount
    elif team == "team_b":
        game_state["team_b_score"] += amount
    await _broadcast_state()


@sio.event
async def set_score(sid, data):
    team = (data or {}).get("team")
    score = int((data or {}).get("score", 0))
    if team == "team_a":
        game_state["team_a_score"] = score
    elif team == "team_b":
        game_state["team_b_score"] = score
    await _broadcast_state()


@sio.event
async def goto_question(sid, data):
    idx = int((data or {}).get("idx", 0))
    if 0 <= idx < len(game_state["questions"]):
        game_state["current_question_idx"] = idx
        _reset_round_state()
    await _broadcast_state()


@sio.event
async def random_question(sid):
    question_count = len(game_state["questions"])
    if question_count == 0:
        await _broadcast_state()
        return
    if question_count == 1:
        game_state["current_question_idx"] = 0
    else:
        choices = [idx for idx in range(question_count) if idx != game_state["current_question_idx"]]
        game_state["current_question_idx"] = random.choice(choices)
    _reset_round_state()
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


@app.get("/api/health")
async def health_check():
    return {
        "ok": True,
        "questions_loaded": len(game_state["questions"]),
        "current_question_idx": game_state["current_question_idx"],
        "database": str(DB_PATH),
    }


@app.post("/api/auth/login")
async def login(payload: LoginPayload):
    expected_password = os.environ.get("ACCESS_PASSWORD", "AI2026")
    if not secrets.compare_digest(payload.password, expected_password):
        raise HTTPException(status_code=401, detail="密碼錯誤")
    token = secrets.token_urlsafe(32)
    active_sessions.add(token)
    return {"token": token}


@app.get("/api/auth/me")
async def auth_me(token: str = Depends(verify_access)):
    return {"authenticated": True}


@app.post("/api/auth/logout")
async def logout(token: str = Depends(verify_access)):
    active_sessions.discard(token)
    return {"ok": True}


@app.get("/api/state", dependencies=[Depends(verify_access)])
async def api_state():
    return JSONResponse(_get_sanitized_state())


@app.get("/api/admin/questions", dependencies=[Depends(verify_access)])
async def list_admin_questions():
    db = load_db(DB_PATH)
    return {"questions": [question_summary(index, question) for index, question in enumerate(db)]}


@app.get("/api/admin/questions/{index}", dependencies=[Depends(verify_access)])
async def get_admin_question(index: int):
    db = load_db(DB_PATH)
    question = validate_index(db, index)
    return {"index": index, **question_summary(index, question), "raw_answers": question.get("raw_answers") or []}


@app.post("/api/admin/questions", dependencies=[Depends(verify_access)], status_code=201)
async def create_admin_question(payload: QuestionCreatePayload):
    question_text = payload.question.strip()

    def create(db: list[dict[str, Any]]):
        if any(item.get("question") == question_text for item in db):
            raise HTTPException(status_code=409, detail="Question already exists")
        entry = {
            "question": question_text,
            "raw_answers": [],
            "raw_answer_records": [],
            "raw_answers_count": 0,
            "model_counts": {},
            "top_answers": serialize_answers(payload.top_answers),
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }
        db.append(entry)
        return {"index": len(db) - 1, "question": entry}

    result, backup_path = mutate_admin_db("create", create)
    load_database()
    await _broadcast_state()
    return {**result, "backup_path": backup_path}


@app.put("/api/admin/questions/{index}", dependencies=[Depends(verify_access)])
async def update_admin_question(index: int, payload: QuestionUpdatePayload):
    question_text = payload.question.strip()

    def update(db: list[dict[str, Any]]):
        current = validate_index(db, index)
        ensure_question_not_running(current.get("question", ""))
        if payload.expected_question and current.get("question") != payload.expected_question:
            raise HTTPException(status_code=409, detail="Question changed since it was loaded")
        if any(i != index and item.get("question") == question_text for i, item in enumerate(db)):
            raise HTTPException(status_code=409, detail="Question already exists")
        updated = dict(current)
        updated["question"] = question_text
        updated["top_answers"] = serialize_answers(payload.top_answers)
        updated["raw_answers_count"] = len(updated.get("raw_answers") or [])
        updated["manually_edited_at"] = now_iso()
        updated["updated_at"] = now_iso()
        db[index] = updated
        return {"index": index, "question": updated}

    result, backup_path = mutate_admin_db("update", update)
    load_database()
    await _broadcast_state()
    return {**result, "backup_path": backup_path}


@app.delete("/api/admin/questions/{index}", dependencies=[Depends(verify_access)])
async def delete_admin_question(index: int):
    def delete(db: list[dict[str, Any]]):
        current = validate_index(db, index)
        ensure_question_not_running(current.get("question", ""))
        removed = db.pop(index)
        return {"deleted": removed.get("question"), "index": index}

    result, backup_path = mutate_admin_db("delete", delete)
    load_database()
    _reset_round_state()
    await _broadcast_state()
    return {**result, "backup_path": backup_path}


@app.get("/api/admin/export", dependencies=[Depends(verify_access)])
async def export_admin_database():
    if not DB_PATH.exists():
        raise HTTPException(status_code=404, detail="Database not found")
    return FileResponse(DB_PATH, filename="questions_db.json", media_type="application/json")


def payload_to_dict(payload: FillAnswersPayload) -> dict[str, Any]:
    if hasattr(payload, "model_dump"):
        return payload.model_dump()
    return payload.dict()


def make_generation_queue_item(
    job_type: str,
    index: int,
    question: str,
    payload: FillAnswersPayload,
    expected_updated_at: str | None = None,
) -> dict[str, Any]:
    payload_data = payload_to_dict(payload)
    payload_data.pop("indexes", None)
    return {
        "id": secrets.token_urlsafe(8),
        "type": job_type,
        "status": "queued",
        "index": index,
        "question": question,
        "queued_at": now_iso(),
        "started_at": None,
        "finished_at": None,
        "error": None,
        "raw_answers_count": None,
        "target_count": payload.target_count,
        "expected_updated_at": expected_updated_at,
        "payload": payload_data,
    }


def get_existing_question_entry(question: str) -> dict[str, Any] | None:
    for entry in load_db(DB_PATH):
        if entry.get("question") == question:
            return entry
    return None


def get_generation_status() -> dict[str, Any]:
    status = json.loads(json.dumps(generation_job))
    current = status.get("current")
    if current:
        entry = get_existing_question_entry(current.get("question", ""))
        if entry is not None:
            current["raw_answers_count"] = len(entry.get("raw_answers") or [])
            status["raw_answers_count"] = current["raw_answers_count"]
        status["question"] = current.get("question")
        status["target_count"] = current.get("target_count")
        status["started_at"] = current.get("started_at")
        status["finished_at"] = current.get("finished_at")
        status["error"] = current.get("error")
    elif status.get("queue"):
        status["status"] = "queued"
        status["question"] = None
        status["started_at"] = None
        status["finished_at"] = None
        status["error"] = None
        status["raw_answers_count"] = None
        status["target_count"] = None
    return status


def start_generation_worker() -> None:
    global generation_worker_task
    if generation_worker_task is None or generation_worker_task.done():
        generation_worker_task = asyncio.create_task(run_generation_queue())


async def enqueue_generation_jobs(jobs: list[dict[str, Any]]) -> dict[str, Any]:
    async with generation_queue_lock:
        generation_job["queue"].extend(jobs)
        if not generation_job.get("current"):
            generation_job["status"] = "queued"
    start_generation_worker()
    return get_generation_status()


def reset_question_answers(question: str) -> None:
    def reset(db: list[dict[str, Any]]):
        for index, entry in enumerate(db):
            if entry.get("question") == question:
                updated = dict(entry)
                updated["raw_answers"] = []
                updated["raw_answer_records"] = []
                updated["raw_answers_count"] = 0
                updated["model_counts"] = {}
                updated["updated_at"] = now_iso()
                db[index] = updated
                return updated
        raise HTTPException(status_code=404, detail="Question not found")

    mutate_admin_db("reset-answers", reset)


def run_renormalization(question: str, payload: FillAnswersPayload) -> None:
    load_env_file(ROOT_DIR / ".env")
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not configured")
    models_config = read_json(MODELS_CONFIG_PATH, None)
    if models_config is None:
        raise RuntimeError(f"Models config not found: {MODELS_CONFIG_PATH}")
    defaults = models_config["defaults"]
    if payload.requests_per_minute is not None:
        defaults["requests_per_minute"] = payload.requests_per_minute
    rate_limiter = RateLimiter(defaults.get("requests_per_minute"))
    entry = get_existing_question_entry(question)
    if entry is None:
        raise RuntimeError("Question was deleted or renamed before this job started")
    raw_answers = list(entry.get("raw_answers") or [])
    if not raw_answers:
        raise RuntimeError("No raw answers available to normalize")
    top_answers = normalize_answers(
        api_key=api_key,
        question=question,
        raw_answers=raw_answers,
        config=defaults,
        rate_limiter=rate_limiter,
    )
    replace_top_answers(DB_PATH, question, top_answers)


def run_generation(question: str, payload: FillAnswersPayload) -> None:
    load_env_file(ROOT_DIR / ".env")
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not configured")
    models_config = read_json(MODELS_CONFIG_PATH, None)
    if models_config is None:
        raise RuntimeError(f"Models config not found: {MODELS_CONFIG_PATH}")
    defaults = models_config["defaults"]
    defaults["total_requests_per_question"] = payload.target_count
    if payload.concurrency is not None:
        defaults["concurrency"] = payload.concurrency
    if payload.requests_per_minute is not None:
        defaults["requests_per_minute"] = payload.requests_per_minute
    rate_limiter = RateLimiter(defaults.get("requests_per_minute"))
    process_question(
        api_key=api_key,
        question=question,
        db_path=DB_PATH,
        models_config=models_config,
        renormalize=payload.renormalize,
        rate_limiter=rate_limiter,
    )


async def run_generation_queue() -> None:
    while True:
        async with generation_queue_lock:
            if not generation_job["queue"]:
                generation_job["current"] = None
                if generation_job["status"] in {"queued", "running"}:
                    generation_job["status"] = "idle"
                return
            job = generation_job["queue"].pop(0)
            job["status"] = "running"
            job["started_at"] = now_iso()
            generation_job.update(
                status="running",
                current=job,
                question=job["question"],
                started_at=job["started_at"],
                finished_at=None,
                error=None,
                raw_answers_count=None,
                target_count=job.get("target_count"),
            )

        try:
            question = job["question"]
            existing_entry = get_existing_question_entry(question)
            if existing_entry is None:
                raise RuntimeError("Question was deleted or renamed before this job started")
            expected_updated_at = job.get("expected_updated_at")
            if expected_updated_at and existing_entry.get("updated_at") != expected_updated_at:
                raise RuntimeError("Question changed after this job was queued; please enqueue it again")
            payload = FillAnswersPayload(**job["payload"])
            if job["type"] == "fill":
                if payload.reset_existing:
                    reset_question_answers(question)
                await asyncio.to_thread(run_generation, question, payload)
            elif job["type"] == "renormalize":
                await asyncio.to_thread(run_renormalization, question, payload)
            else:
                raise RuntimeError(f"Unknown generation job type: {job['type']}")

            load_database()
            await _broadcast_state()
            entry = get_question_entry(DB_PATH, question)
            job.update(
                status="completed",
                finished_at=now_iso(),
                raw_answers_count=len(entry.get("raw_answers") or []),
                error=None,
            )
            async with generation_queue_lock:
                generation_job["completed_jobs"] = [job, *generation_job["completed_jobs"][:19]]
                generation_job.update(
                    status="completed",
                    finished_at=job["finished_at"],
                    raw_answers_count=job["raw_answers_count"],
                    error=None,
                )
        except Exception as exc:
            job.update(status="failed", finished_at=now_iso(), error=str(exc))
            async with generation_queue_lock:
                generation_job["failed_jobs"] = [job, *generation_job["failed_jobs"][:19]]
                generation_job.update(status="failed", finished_at=job["finished_at"], error=str(exc))
        finally:
            async with generation_queue_lock:
                generation_job["current"] = None


@app.post("/api/admin/questions/{index}/fill", dependencies=[Depends(verify_access)], status_code=202)
async def fill_admin_question(index: int, payload: FillAnswersPayload):
    db = load_db(DB_PATH)
    entry = validate_index(db, index)
    job = make_generation_queue_item("fill", index, entry.get("question", ""), payload, entry.get("updated_at"))
    return await enqueue_generation_jobs([job])


@app.post("/api/admin/questions/{index}/renormalize", dependencies=[Depends(verify_access)], status_code=202)
async def renormalize_admin_question(index: int, payload: FillAnswersPayload):
    db = load_db(DB_PATH)
    entry = validate_index(db, index)
    if not entry.get("raw_answers"):
        raise HTTPException(status_code=400, detail="This question has no raw answers to normalize")
    job = make_generation_queue_item("renormalize", index, entry.get("question", ""), payload, entry.get("updated_at"))
    return await enqueue_generation_jobs([job])


@app.get("/api/admin/generation-job", dependencies=[Depends(verify_access)])
async def get_generation_job():
    return get_generation_status()


def _reset_round_state() -> None:
    game_state["revealed_answers"] = []
    game_state["strikes"] = 0
    game_state["current_pool"] = 0


def _get_sanitized_state() -> dict[str, Any]:
    safe_state = json.loads(json.dumps(game_state))
    if safe_state["questions"]:
        current = safe_state["questions"][safe_state["current_question_idx"]]
        safe_answers = [
            answer if index in safe_state["revealed_answers"] else {"answer": "???", "count": 0}
            for index, answer in enumerate(current.get("top_answers", []))
        ]
        safe_state["current_question_data"] = {
            "question": current.get("question"),
            "answers": safe_answers,
        }
    else:
        safe_state["current_question_data"] = None
    del safe_state["questions"]
    return safe_state


async def _broadcast_state() -> None:
    await sio.emit("state_update", _get_sanitized_state())
    await sio.emit("host_state_update", game_state)


@app.on_event("startup")
async def startup_event():
    load_database()


@app.get("/{full_path:path}")
async def serve_client(full_path: str):
    if not CLIENT_DIST_DIR.exists():
        return JSONResponse(
            {
                "message": "Client build not found. Run `cd client && npm run build`, then restart the server.",
                "api": "/api/health",
            },
            status_code=404,
        )
    requested_path = CLIENT_DIST_DIR / full_path
    if full_path and requested_path.is_file():
        return FileResponse(requested_path)
    index_path = CLIENT_DIST_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return JSONResponse({"message": "Client index.html not found."}, status_code=404)


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("app:socket_app", host="0.0.0.0", port=port, reload=True)
