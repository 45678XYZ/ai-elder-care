"""階段三：FastAPI，提供 /ask 給 Flutter app 呼叫。RAG 邏輯完全重用 rag.py。"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from rag import answer

app = FastAPI(title="長照問答 RAG")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class AskRequest(BaseModel):
    question: str


class Source(BaseModel):
    title: str
    url: str


class AskResponse(BaseModel):
    answer: str
    sources: list[Source]


@app.post("/ask", response_model=AskResponse)
def ask(req: AskRequest) -> dict:
    return answer(req.question)
