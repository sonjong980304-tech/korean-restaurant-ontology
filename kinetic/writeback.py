"""온톨로지 쓰기(write-back) 기록.

팔란티어에서 사용자가 온톨로지 위에서 한 편집은 원천 데이터와 별도로 'write-back 데이터셋'에 쌓이고,
원천에서 온톨로지를 다시 만들 때 그 위에 다시 덮어씌워진다. 여기서는 JSON Lines 파일 한 개가 그 역할을 한다.
  - append: 액션·시나리오·결정을 실행할 때 한 줄씩 추가
  - replay: 파이프라인 재적재 후 기록 순서대로 다시 실행(같은 id·시각을 그대로 써서 결과가 같다)
"""
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOG = ROOT / "data" / "writeback" / "events.jsonl"


def append(log_path, event):
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def read(log_path):
    path = Path(log_path)
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def replay(driver, log_path=DEFAULT_LOG):
    """기록된 쓰기를 순서대로 다시 실행한다. 다시 실행할 때는 기록을 새로 쓰지 않는다."""
    from kinetic import actions

    handlers = {"action": actions.replay_event}
    try:
        from dynamic import decision, scenario

        handlers.update({"scenario": scenario.replay_event, "decision": decision.replay_event})
    except ImportError:
        pass
    events = read(log_path)
    for event in events:
        handlers[event["kind"]](driver, event)
    return len(events)


def stamp(replayed):
    """새 실행이면 지금 시각, 재생이면 기록된 시각을 쓴다."""
    if replayed:
        return datetime.fromisoformat(replayed["executed_at"])
    return datetime.now()                         # 마이크로초까지 남겨 같은 초 안의 순서도 보존
