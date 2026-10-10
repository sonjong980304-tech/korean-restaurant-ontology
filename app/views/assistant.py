"""Dynamic — GraphRAG AI 매니저. 그래프에서 찾은 근거로만 답하고, 액션은 제안만 한다."""
import html
import json

import pandas as pd
import streamlit as st
from common import db_or_stop, refresh, setup

from dynamic import graphrag as G
from kinetic import actions as A

setup("AI 매니저", "DYNAMIC · GRAPHRAG",
      "질문하면 온톨로지 함수와 읽기 전용 그래프 질의로 근거를 찾아 답합니다. 가격·품절·발주는 제안만 하고, 실행은 승인 버튼으로 합니다.")
d = db_or_stop()
chat = st.session_state.setdefault("chat", [])
EXAMPLES = ["9월에 어떤 손님을 가장 많이 놓쳤어?", "양파값이 40% 더 오르면 어떤 메뉴가 가장 타격받아? 대응도 제안해줘",
            "10월 2일 금요일에 부족할 재료는?", "육류를 대성미트로 바꾸면 9월 기준 얼마 아껴?"]
ACTION_KO = {"change_price": "가격 변경", "set_availability": "품절·판매 재개", "create_purchase_order": "발주"}


def show_evidence(evidence):
    with st.expander(f"근거 — 도구 {len(evidence)}번 호출"):
        for e in evidence:
            st.markdown(f"**{e['tool']}** `{json.dumps(e['args'], ensure_ascii=False)}`")
            res = e["result"]
            rows = res if isinstance(res, list) else res.get("rows") or res.get("menus") or res.get("ingredients") \
                if isinstance(res, dict) else None
            if isinstance(rows, list) and rows and isinstance(rows[0], dict):
                st.dataframe(pd.DataFrame(rows).head(30), hide_index=True, use_container_width=True)
            else:
                st.json(res, expanded=False)


def show_proposals(i, proposals):
    for j, p in enumerate(proposals):
        done = st.session_state.get(f"done_{i}_{j}")
        try:                                   # 승인 화면에는 서버가 계산한 '실제로 바뀌는 내용'을 보여 준다
            preview, ok = A.describe_proposal(d, p), True
        except ValueError as e:
            preview, ok = f"실행할 수 없는 제안: {e}", False
        st.markdown(f'<div class="card"><b>제안 — {html.escape(ACTION_KO.get(str(p.get("action_type")), "알 수 없음"))}</b><br>'
                    f'{html.escape(preview)}<br><span style="color:#8a897f">AI가 든 이유: '
                    f'{html.escape(str(p.get("reason", "")))}</span></div>', unsafe_allow_html=True)
        if done:
            (st.success if done["status"] == "실행" else st.error)(f"{done['status']} — {done['message']}")
        elif ok and st.button("승인하고 실행", key=f"ok_{i}_{j}", type="primary"):
            st.session_state[f"done_{i}_{j}"] = A.execute_proposal(d, p, actor="사장(AI 제안 승인)")
            refresh()
            st.rerun()


for i, m in enumerate(chat):
    with st.chat_message(m["role"]):
        st.markdown(m["content"])
        if m["role"] == "assistant":
            if m.get("evidence"):
                show_evidence(m["evidence"])
            show_proposals(i, m.get("proposals", []))

if not chat:
    st.caption("예시 질문")
    cols = st.columns(2)
    for k, q in enumerate(EXAMPLES):
        if cols[k % 2].button(q, key=f"ex_{k}", use_container_width=True):
            st.session_state["pending"] = q
            st.rerun()

question = st.chat_input("가게에 대해 물어보세요") or st.session_state.pop("pending", None)
if question:
    history = [{"role": m["role"], "content": m["content"]} for m in chat[-6:]]
    chat.append({"role": "user", "content": question})
    with st.spinner("그래프에서 근거를 찾는 중…"):
        try:
            out = G.ask(d, question, history=history)
        except Exception as e:  # noqa: BLE001
            # 오류 원문에는 키 일부 같은 민감한 값이 섞일 수 있어 종류만 보여 준다
            out = {"answer": f"AI 호출에 실패했습니다({type(e).__name__}). 잠시 후 다시 시도하거나 .env의 API 키를 확인하세요.",
                   "evidence": [], "proposals": []}
    chat.append({"role": "assistant", "content": out["answer"], "evidence": out["evidence"], "proposals": out["proposals"]})
    st.rerun()
