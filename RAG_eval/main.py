import os
import json
import math
import asyncio
import logging
import requests
from dotenv import load_dotenv
from typing import List, Dict, Any
from langchain_openai import ChatOpenAI
from ragas.llms import LangchainLLMWrapper
from langchain.embeddings.base import Embeddings
from ragas.dataset_schema import SingleTurnSample
from ragas.embeddings import LangchainEmbeddingsWrapper
from ragas.metrics import Faithfulness, ResponseRelevancy, LLMContextPrecisionWithoutReference, LLMContextRecall

load_dotenv(dotenv_path=".env")

# Configure logging format and level
logging.basicConfig(
    level=logging.INFO,  # Minimum log level
    format="%(asctime)s [%(levelname)s] %(message)s"
)

class SiliconFlowEmbeddings(Embeddings):
    def __init__(self, model: str = "BAAI/bge-m3", api_key: str = None):
        self.model = model
        self.api_key = api_key or os.environ.get("SILICONFLOW_API_KEY")
        if not self.api_key:
            raise ValueError("Please set the environment variable SILICONFLOW_API_KEY or pass api_key during initialization.")

    def _embedding_request(self, inputs: List[str]) -> List[List[float]]:
        url = os.environ.get("EMBEDDING_BASE_URL")
        payload = {
            "model": self.model,
            "input": inputs
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        response = requests.post(url, json=payload, headers=headers)
        res_json = response.json()
        if "data" in res_json and len(res_json["data"]) > 0:
            return [item["embedding"] for item in res_json["data"]]
        else:
            raise RuntimeError(f"Failed to get embeddings: {res_json}")

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        result = []
        batch_size = 64
        for i in range(0, len(texts), batch_size):
            batch_embeddings = self._embedding_request(texts[i:i+batch_size])
            result.extend(batch_embeddings)
        return result

    def embed_query(self, text: str) -> List[float]:
        return self.embed_documents([text])[0]

# Reference context list for each question according to your knowledge base
reference_context_list = []

# Initialize LLM
llm = ChatOpenAI(
    model_name=os.environ.get("LLM_MODEL_NAME"),
    temperature=0,
    api_key=os.environ.get("OPENAI_API_KEY"),
    base_url=os.environ.get("OPENAI_BASE_URL"), 
    max_completion_tokens=os.environ.get("MAX_COMPLETION_TOKENS"),
)

evaluator_llm = LangchainLLMWrapper(llm)
evaluator_embeddings = LangchainEmbeddingsWrapper(SiliconFlowEmbeddings())

# Initialize evaluators
faithfulness = Faithfulness(llm=evaluator_llm)
ctx_precision = LLMContextPrecisionWithoutReference(llm=evaluator_llm)
ctx_recall = LLMContextRecall(llm=evaluator_llm)
resp_relevancy = ResponseRelevancy(llm=evaluator_llm, embeddings=evaluator_embeddings)

# Question list according to your knowledge base
questions = []

# ====== Utility functions ======
def _norm_context(ctx: Any) -> List[str]:
    if ctx is None:
        return []
    if isinstance(ctx, list):
        items = []
        for x in ctx:
            if isinstance(x, str):
                s = " ".join(x.split())
                if s:
                    items.append(s)
            else:
                s = str(x).strip()
                if s:
                    items.append(s)
    else:
        s = str(ctx).strip()
        items = [s] if s else []
    # Remove duplicates while preserving order
    seen, out = set(), []
    for s in items:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out

def _is_nan(x: float) -> bool:
    return isinstance(x, float) and math.isnan(x)

async def evaluate_all():
    with open("lightrag_qa_results.json", "r", encoding="utf-8") as f:
        data: List[Dict[str, Any]] = json.load(f)

    if len(data) < len(questions):
        logging.warning("Number of items in file %d is less than number of questions %d, only evaluating available items.", len(data), len(questions))
    per_item_scores = []  
    # For average calculation
    sums = {"faithfulness": 0.0, "ctx_precision": 0.0, "ctx_recall": 0.0, "resp_relevancy": 0.0}
    counts = {"faithfulness": 0, "ctx_precision": 0, "ctx_recall": 0, "resp_relevancy": 0}

    N = min(len(questions), len(data))
    for i in range(N):
        q = questions[i]
        item = data[i]

        response = item.get("llm_output", "") or item.get("answer", "")
        contexts = _norm_context(item.get("context") or item.get("contexts"))
        reference = ",".join(reference_context_list[i])  

        sample = SingleTurnSample(
            user_input=q,
            response=response,
            retrieved_contexts=contexts,
            reference=reference,  
        )

        row = {"question": q}
        # ---- Faithfulness ----
        try:
            f_score = await faithfulness.single_turn_ascore(sample)
            row["faithfulness"] = f_score
            if not _is_nan(f_score):
                sums["faithfulness"] += f_score
                counts["faithfulness"] += 1
            logging.info("[%d/%d] Faithfulness: %.4f", i + 1, N, f_score)
        except Exception as e:
            row["faithfulness"] = None
            logging.error("[%d/%d] Faithfulness failed: %s", i + 1, N, e)

        # ---- LLMContextPrecisionWithoutReference ----
        try:
            cp_score = await ctx_precision.single_turn_ascore(sample)
            row["context_precision_wo_ref"] = cp_score
            if not _is_nan(cp_score):
                sums["ctx_precision"] += cp_score
                counts["ctx_precision"] += 1
                logging.info("   └ ContextPrecision(wo ref): %.4f", cp_score)
        except Exception as e:
            row["context_precision_wo_ref"] = None
            logging.error("   └ ContextPrecision failed: %s", e)

        # ---- LLMContextRecall ----
        if reference:
            try:
                cr_score = await ctx_recall.single_turn_ascore(sample)
                row["context_recall"] = cr_score
                if not _is_nan(cr_score):
                    sums["ctx_recall"] += cr_score
                    counts["ctx_recall"] += 1
                logging.info("   └ ContextRecall: %.4f", cr_score)
            except Exception as e:
                row["context_recall"] = None
                logging.error("   └ ContextRecall failed: %s", e)
        else:
            row["context_recall"] = None
            logging.warning("   └ ContextRecall skipped (no reference/ground_truth)")

        # ---- ResponseRelevancy ----
        try:
            rr_score = await resp_relevancy.single_turn_ascore(sample)
            row["response_relevancy"] = rr_score
            if not _is_nan(rr_score):
                sums["resp_relevancy"] += rr_score
                counts["resp_relevancy"] += 1
            logging.info("   └ ResponseRelevancy: %.4f", rr_score)
        except Exception as e:
            row["response_relevancy"] = None
            logging.error("   └ ResponseRelevancy failed: %s", e)

        per_item_scores.append(row)

    avg = {
        k: (sums[k] / counts[k] if counts[k] > 0 else None)
        for k in sums
    }

    logging.info("Average Scores:")
    logging.info("   Faithfulness              : %s", avg["faithfulness"])
    logging.info("   ContextPrecision (wo ref) : %s", avg["ctx_precision"])
    logging.info("   ContextRecall             : %s (only samples with reference)", avg["ctx_recall"])
    logging.info("   ResponseRelevancy         : %s", avg["resp_relevancy"])

    output = {
        "per_item": per_item_scores,
        "averages": {
            "faithfulness": avg["faithfulness"],
            "context_precision_wo_ref": avg["ctx_precision"],
            "context_recall": avg["ctx_recall"],
            "response_relevancy": avg["resp_relevancy"],
            "counts": counts,
        },
    }
    with open("ragas_scores.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    asyncio.run(evaluate_all())
