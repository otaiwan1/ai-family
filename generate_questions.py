import argparse
import json
import os
import random
import re
import time
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

import requests

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None


ROOT_DIR = Path(__file__).resolve().parent
DEFAULT_QUESTIONS_FILE = ROOT_DIR / "questions.txt"
DEFAULT_DB_FILE = ROOT_DIR / "questions_db.json"
DEFAULT_MODELS_FILE = ROOT_DIR / "openrouter_models.json"
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"


class RateLimiter:
    def __init__(self, requests_per_minute: int | float | None) -> None:
        self.min_interval_seconds = 0.0
        if requests_per_minute and requests_per_minute > 0:
            self.min_interval_seconds = 60.0 / float(requests_per_minute)
        self._lock = Lock()
        self._next_request_at = 0.0

    def wait(self) -> None:
        if self.min_interval_seconds <= 0:
            return

        with self._lock:
            now = time.monotonic()
            wait_seconds = max(0.0, self._next_request_at - now)
            self._next_request_at = max(now, self._next_request_at) + self.min_interval_seconds

        if wait_seconds > 0:
            time.sleep(wait_seconds)


def load_env_file(path: Path) -> None:
    if not path.exists():
        return

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    content = path.read_text(encoding="utf-8").strip()
    if not content:
        return fallback
    return json.loads(content)


def atomic_write_json(path: Path, data: Any) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp_path, path)


@contextmanager
def db_file_lock(path: Path):
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        if fcntl is not None:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def mutate_db(db_path: Path, updater):
    with db_file_lock(db_path):
        db = load_db(db_path)
        result = updater(db)
        atomic_write_json(db_path, db)
        return result


def load_questions(path: Path) -> list[str]:
    if not path.exists():
        raise FileNotFoundError(f"找不到題目檔案: {path}")
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_db(path: Path) -> list[dict[str, Any]]:
    data = read_json(path, [])
    if not isinstance(data, list):
        raise ValueError(f"{path} 必須是 JSON array")
    return data


def find_entry(db: list[dict[str, Any]], question: str) -> tuple[int | None, dict[str, Any] | None]:
    for idx, item in enumerate(db):
        if item.get("question") == question:
            return idx, item
    return None, None


def update_question_entry(db_path: Path, question: str, updater) -> dict[str, Any]:
    def apply_update(db: list[dict[str, Any]]) -> dict[str, Any]:
        idx, current = find_entry(db, question)
        if current is None:
            current = {"question": question, "raw_answers": [], "raw_answers_count": 0}
            db.append(current)
            idx = len(db) - 1

        updated = updater(dict(current))
        updated["question"] = question
        updated["updated_at"] = now_iso()
        db[idx] = updated
        return updated

    return mutate_db(db_path, apply_update)


def get_question_entry(db_path: Path, question: str) -> dict[str, Any]:
    _, entry = find_entry(load_db(db_path), question)
    return entry or {"question": question, "raw_answers": [], "raw_answers_count": 0}


def clean_single_answer(text: str) -> str:
    answer = text.strip().splitlines()[0].strip()
    answer = re.sub(r"^[\s\d\.\-\)\(、:：]+", "", answer)
    answer = answer.strip(" \t\r\n\"'「」『』，。,.!?！？；;：:")
    answer = fix_mojibake(answer)
    answer = answer.strip(" \t\r\n\"'「」『』，。,.!?！？；;：:")
    return answer[:40]


def fix_mojibake(text: str) -> str:
    mojibake_markers = ("Ã", "Â", "å", "æ", "ç", "è", "é", "\x80", "\x81", "\x82", "\x83", "\x84", "\x85")
    if not any(marker in text for marker in mojibake_markers):
        return text
    try:
        repaired = text.encode("latin1").decode("utf-8")
    except UnicodeError:
        return text
    return repaired if repaired else text


def parse_response_json(response: requests.Response) -> dict[str, Any]:
    return json.loads(response.content.decode("utf-8"))


def extract_message_text(data: dict[str, Any]) -> str:
    if data.get("error"):
        raise RuntimeError(f"OpenRouter error: {data['error']}")

    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(f"OpenRouter response has no choices: {data}")

    choice = choices[0]
    message = choice.get("message") or {}
    content = message.get("content")

    if isinstance(content, str) and content.strip():
        return fix_mojibake(content).strip()

    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                part_text = part.get("text") or part.get("content")
                if isinstance(part_text, str):
                    parts.append(part_text)
        joined = "".join(parts).strip()
        if joined:
            return fix_mojibake(joined).strip()

    for fallback_key in ("reasoning", "reasoning_content"):
        fallback = message.get(fallback_key)
        if isinstance(fallback, str) and fallback.strip():
            raise RuntimeError(
                f"OpenRouter returned reasoning but no final content "
                f"(finish_reason={choice.get('finish_reason')})"
            )

    raise RuntimeError(
        "OpenRouter returned empty content "
        f"(finish_reason={choice.get('finish_reason')}, "
        f"native_finish_reason={choice.get('native_finish_reason')}, "
        f"message_keys={list(message.keys())})"
    )


def merge_parameters(base: dict[str, Any], override: dict[str, Any] | None) -> dict[str, Any]:
    merged = dict(base)
    for key, value in (override or {}).items():
        if value is None:
            merged.pop(key, None)
        elif isinstance(value, dict) and isinstance(merged.get(key), dict):
            nested = dict(merged[key])
            for nested_key, nested_value in value.items():
                if nested_value is None:
                    nested.pop(nested_key, None)
                else:
                    nested[nested_key] = nested_value
            merged[key] = nested
        else:
            merged[key] = value
    return merged


def openrouter_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://localhost/ai-family-feud",
        "X-Title": "AI Family Feud Question Generator",
    }


def chat_completion(
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    params: dict[str, Any],
    timeout: int,
    max_retries: int,
    rate_limiter: RateLimiter,
) -> str:
    payload = {
        "model": model,
        "messages": messages,
        **params,
    }

    for attempt in range(max_retries + 1):
        try:
            rate_limiter.wait()
            response = requests.post(
                OPENROUTER_API_URL,
                headers=openrouter_headers(api_key),
                json=payload,
                timeout=timeout,
            )
            if response.status_code in {401, 402, 403, 404}:
                raise RuntimeError(f"OpenRouter {response.status_code}: {response.text}")
            if response.status_code == 429:
                retry_after = int(response.headers.get("retry-after", "20"))
                print(f"  -> rate limited，等待 {retry_after} 秒後重試...")
                time.sleep(retry_after)
                continue
            if response.status_code >= 400:
                raise RuntimeError(f"OpenRouter {response.status_code}: {response.text}")
            response.raise_for_status()
            data = parse_response_json(response)
            return extract_message_text(data)
        except (requests.RequestException, RuntimeError, KeyError, IndexError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            if attempt >= max_retries:
                raise RuntimeError(f"OpenRouter request failed after retries: {exc}") from exc
            wait_seconds = min(60, 5 * (2**attempt))
            print(f"  -> request error: {exc}，等待 {wait_seconds} 秒後重試...")
            time.sleep(wait_seconds)

    raise RuntimeError("OpenRouter request failed")


def get_single_answer(
    api_key: str,
    question: str,
    model: dict[str, Any],
    config: dict[str, Any],
    rate_limiter: RateLimiter,
) -> str:
    model_id = model["id"]
    system_prompt = (
        "你正在參與台灣街頭隨機問卷。請像一位真實、直覺、稍微有創意的受訪者回答。"
        "只輸出一個繁體中文答案，答案必須是一個名詞或極短詞語，約 1 到 8 個中文字。"
        "不要解釋，不要標點，不要換行，不要列舉，不要加引號。"
    )
    user_prompt = f"問題：{question}\n請只回答一個最直覺的答案。"
    params = merge_parameters(config["answer_generation"], model.get("answer_generation"))
    raw = chat_completion(
        api_key=api_key,
        model=model_id,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        params=params,
        timeout=config.get("request_timeout_seconds", 90),
        max_retries=config.get("max_retries", 2),
        rate_limiter=rate_limiter,
    )
    return clean_single_answer(raw)


def normalize_answers(
    api_key: str,
    question: str,
    raw_answers: list[str],
    config: dict[str, Any],
    rate_limiter: RateLimiter,
) -> list[dict[str, Any]]:
    answers_text = "\n".join(f"- {answer}" for answer in raw_answers if answer)
    prompt = (
        f"我問了多個 AI 以下問題：「{question}」。\n"
        f"以下是所有原始回答：\n{answers_text}\n\n"
        "請將意思相同或高度相似的答案合併，例如「蘋果」「紅蘋果」「Apple」合併為「蘋果」。"
        "請統計合併後的次數，列出最適合遊戲使用的 Top 4 到 Top 8。"
        "答案名稱請短、口語、適合出現在家庭大哉問題板上。"
        "只回傳 JSON array，不要 Markdown，不要說明文字。"
        "格式必須是 [{\"answer\":\"答案\",\"count\":12}]。"
    )
    raw = chat_completion(
        api_key=api_key,
        model=config["normalization"]["model"],
        messages=[{"role": "user", "content": prompt}],
        params=config["normalization"]["parameters"],
        timeout=config.get("request_timeout_seconds", 90),
        max_retries=config.get("max_retries", 2),
        rate_limiter=rate_limiter,
    )

    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if match:
        raw = match.group(0)
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError("normalization result must be a JSON array")
    return [
        {"answer": str(item["answer"]).strip(), "count": int(item["count"])}
        for item in data
        if item.get("answer") and int(item.get("count", 0)) > 0
    ]


def model_plan(models_config: dict[str, Any], remaining: int) -> list[dict[str, Any]]:
    enabled = [model for model in models_config["models"] if model.get("enabled", True)]
    weighted: list[dict[str, Any]] = []
    for model in enabled:
        weighted.extend([model] * int(model.get("requests", 0)))
    if not weighted:
        raise ValueError("openrouter_models.json 至少需要一個 enabled model 且 requests > 0")

    random.shuffle(weighted)
    if remaining <= len(weighted):
        return weighted[:remaining]

    tasks: list[dict[str, Any]] = []
    while len(tasks) < remaining:
        random.shuffle(weighted)
        tasks.extend(weighted)
    return tasks[:remaining]


def append_raw_answer(db_path: Path, question: str, answer: str, model_id: str) -> dict[str, Any]:
    def updater(entry: dict[str, Any]) -> dict[str, Any]:
        raw_answers = list(entry.get("raw_answers") or [])
        records = list(entry.get("raw_answer_records") or [])
        cleaned_answer = clean_single_answer(answer)
        raw_answers.append(cleaned_answer)
        records.append({"answer": cleaned_answer, "model": model_id, "created_at": now_iso()})
        entry["raw_answers"] = raw_answers
        entry["raw_answer_records"] = records
        entry["raw_answers_count"] = len(raw_answers)
        model_counts: dict[str, int] = {}
        for record in records:
            record_model = record.get("model", "unknown")
            model_counts[record_model] = model_counts.get(record_model, 0) + 1
        entry["model_counts"] = model_counts
        return entry

    return update_question_entry(db_path, question, updater)


def save_top_answers(db_path: Path, question: str, top_answers: list[dict[str, Any]]) -> dict[str, Any]:
    def updater(entry: dict[str, Any]) -> dict[str, Any]:
        if entry.get("top_answers"):
            print(f"[{question}] 已有 top_answers，保留現有正規化結果。若要重算請加 --renormalize。")
            return entry
        entry["top_answers"] = top_answers
        entry["normalized_at"] = now_iso()
        return entry

    return update_question_entry(db_path, question, updater)


def replace_top_answers(db_path: Path, question: str, top_answers: list[dict[str, Any]]) -> dict[str, Any]:
    def updater(entry: dict[str, Any]) -> dict[str, Any]:
        entry["top_answers"] = top_answers
        entry["normalized_at"] = now_iso()
        return entry

    return update_question_entry(db_path, question, updater)


def process_question(
    api_key: str,
    question: str,
    db_path: Path,
    models_config: dict[str, Any],
    renormalize: bool,
    rate_limiter: RateLimiter,
) -> None:
    defaults = models_config["defaults"]
    target_count = int(defaults["total_requests_per_question"])
    max_attempts = int(defaults.get("max_generation_attempts_per_question", target_count * 3))
    attempts = 0

    while True:
        entry = get_question_entry(db_path, question)
        raw_answers = list(entry.get("raw_answers") or [])
        if len(raw_answers) >= target_count:
            break

        remaining = target_count - len(raw_answers)
        remaining_attempts = max_attempts - attempts
        if remaining_attempts <= 0:
            print(f"[{question}] 已達最大嘗試次數 {max_attempts}，目前 {len(raw_answers)}/{target_count}")
            break

        batch_size = min(remaining, remaining_attempts)
        tasks = model_plan(models_config, batch_size)
        print(f"\n[{question}] 補齊 {remaining} 次回答，目前 {len(raw_answers)}/{target_count}")

        concurrency = int(defaults.get("concurrency", 3))
        requests_per_minute = defaults.get("requests_per_minute")
        speed_label = "unlimited" if not requests_per_minute else f"{requests_per_minute}/min"
        print(f"  -> concurrency={concurrency}, request limit={speed_label}, attempts={attempts}/{max_attempts}")

        successes = 0
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            future_map = {
                executor.submit(get_single_answer, api_key, question, model, defaults, rate_limiter): model
                for model in tasks
            }
            attempts += len(future_map)
            for future in as_completed(future_map):
                model = future_map[future]
                try:
                    answer = future.result()
                except Exception as exc:
                    print(f"  -> {model['id']} 失敗: {exc}")
                    continue
                if not answer:
                    print(f"  -> {model['id']} 回傳空答案，略過")
                    continue
                updated = append_raw_answer(db_path, question, answer, model["id"])
                successes += 1
                print(f"  -> {updated['raw_answers_count']}/{target_count} {model['id']}: {answer}")

        if successes == 0:
            print(f"[{question}] 本輪沒有成功答案，等待 10 秒後再補發...")
            time.sleep(10)

    latest = get_question_entry(db_path, question)
    latest_raw_answers = list(latest.get("raw_answers") or [])
    has_top_answers = bool(latest.get("top_answers"))
    if len(latest_raw_answers) >= target_count and (renormalize or not has_top_answers):
        print(f"[{question}] 開始正規化 {len(latest_raw_answers)} 筆答案...")
        top_answers = normalize_answers(api_key, question, latest_raw_answers, defaults, rate_limiter)
        if renormalize:
            replace_top_answers(db_path, question, top_answers)
        else:
            save_top_answers(db_path, question, top_answers)
        print(f"[{question}] 正規化完成: {top_answers}")
    elif has_top_answers:
        print(f"[{question}] 已有 top_answers，跳過正規化。")
    else:
        print(f"[{question}] 有效回答不足 {target_count}，暫不正規化。")


def validate_models(models_config: dict[str, Any]) -> None:
    response = requests.get(OPENROUTER_MODELS_URL, timeout=30)
    response.raise_for_status()
    models = response.json().get("data", [])
    available = {model.get("id"): model for model in models}
    print(f"OpenRouter models fetched: {len(available)}")

    for model in models_config["models"]:
        model_id = model["id"]
        found = available.get(model_id)
        if found:
            print(f"OK  {model_id} | {found.get('name')}")
        else:
            print(f"MISS {model_id}")

    normalization_model = models_config["defaults"]["normalization"]["model"]
    if normalization_model in available:
        print(f"OK  normalization model: {normalization_model}")
    else:
        print(f"MISS normalization model: {normalization_model}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate AI Family Feud question database with OpenRouter.")
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS_FILE)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_FILE)
    parser.add_argument("--models", type=Path, default=DEFAULT_MODELS_FILE)
    parser.add_argument("--validate-models", action="store_true", help="Only check whether configured OpenRouter models exist.")
    parser.add_argument("--renormalize", action="store_true", help="Recompute top_answers even if they already exist.")
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N questions from the questions file.")
    parser.add_argument("--concurrency", type=int, default=None, help="Override defaults.concurrency from openrouter_models.json.")
    parser.add_argument("--rpm", type=float, default=None, help="Override defaults.requests_per_minute from openrouter_models.json. Use 0 for no local throttle.")
    args = parser.parse_args()

    load_env_file(ROOT_DIR / ".env")
    models_config = read_json(args.models, None)
    if models_config is None:
        raise FileNotFoundError(f"找不到模型設定檔: {args.models}")

    if args.validate_models:
        validate_models(models_config)
        return

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("找不到 OPENROUTER_API_KEY，請寫在 .env 或環境變數。")

    if args.concurrency is not None:
        models_config["defaults"]["concurrency"] = args.concurrency
    if args.rpm is not None:
        models_config["defaults"]["requests_per_minute"] = args.rpm

    rate_limiter = RateLimiter(models_config["defaults"].get("requests_per_minute"))

    questions = load_questions(args.questions)
    if args.limit is not None:
        questions = questions[: args.limit]

    print(f"載入 {len(questions)} 題，DB: {args.db}")
    for question in questions:
        process_question(
            api_key=api_key,
            question=question,
            db_path=args.db,
            models_config=models_config,
            renormalize=args.renormalize,
            rate_limiter=rate_limiter,
        )

    print("\n所有可處理題目已完成。")


if __name__ == "__main__":
    main()
