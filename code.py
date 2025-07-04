#Setup
!pip install -q datasets transformers peft accelerate evaluate rouge_score nltk bert_score

import os
import torch
from datasets import load_dataset
from transformers import AutoTokenizer, AutoModelForSeq2SeqLM, TrainingArguments, Trainer
from peft import get_peft_model, LoraConfig, TaskType
from huggingface_hub import login

login("YOUR-HUGGINGFACE-LOGIN-KEY")

BASE_MODEL = "google/flan-t5-base"
TOKENIZER = AutoTokenizer.from_pretrained(BASE_MODEL)
model = AutoModelForSeq2SeqLM.from_pretrained(BASE_MODEL)

#Load and Preprocess Dataset
dataset = load_dataset("ccdv/arxiv-summarization")
dataset = dataset["train"].shuffle(seed=42).select(range(5000))

#Preprocessing
def preprocess(example):
    return {
        "input_text": "summarize: " + example["article"],
        "target_text": example["abstract"]
    }

dataset = dataset.map(preprocess)

#Tokenization
def tokenize(example):
    model_inputs = TOKENIZER(
        example["input_text"], max_length=512, truncation=True, padding="max_length"
    )
    labels = TOKENIZER(
        example["target_text"], max_length=128, truncation=True, padding="max_length"
    )
    model_inputs["labels"] = labels["input_ids"]
    return model_inputs

tokenized_dataset = dataset.map(tokenize, batched=False)

#Train/Val/Test Split
train_size = int(0.8 * len(tokenized_dataset))
val_size = int(0.1 * len(tokenized_dataset))

train_data = tokenized_dataset.select(range(train_size))
val_data = tokenized_dataset.select(range(train_size, train_size + val_size))
test_data = tokenized_dataset.select(range(train_size + val_size, len(tokenized_dataset)))

#Install bitsandbytes properly (latest version)
!pip install -q --upgrade bitsandbytes
!pip install -q --upgrade accelerate transformers peft

!pip uninstall -y bitsandbytes
!pip install -U bitsandbytes accelerate transformers datasets peft trl

#Fine-Tuning with LoRA (no quantization)
model = AutoModelForSeq2SeqLM.from_pretrained(BASE_MODEL)


lora_config = LoraConfig(
    r=8,
    lora_alpha=16,
    target_modules=["q", "v"],
    lora_dropout=0.1,
    bias="none",
    task_type=TaskType.SEQ_2_SEQ_LM
)

model = get_peft_model(model, lora_config)

training_args = TrainingArguments(
    output_dir="./lora_summarizer_t5",
    per_device_train_batch_size=4,
    per_device_eval_batch_size=4,
    num_train_epochs=4,
    eval_strategy="epoch",
    save_strategy="epoch",
    logging_dir="./logs",
    fp16=True,
)

#Dataset wrapper
class Dataset(torch.utils.data.Dataset):
    def __init__(self, data):
        self.data = data
    def __len__(self):
        return len(self.data)
    def __getitem__(self, idx):
        return {
            "input_ids": torch.tensor(self.data[idx]["input_ids"]),
            "attention_mask": torch.tensor(self.data[idx]["attention_mask"]),
            "labels": torch.tensor(self.data[idx]["labels"]),
        }

trainer = Trainer(
    model=model,
    args=training_args,
    train_dataset=Dataset(train_data),
    eval_dataset=Dataset(val_data),
    tokenizer=TOKENIZER,
)

trainer.train()

model.save_pretrained("finetuned-t5-lora")

import random

def generate_summary(model, input_text):
    input_ids = TOKENIZER(input_text, return_tensors="pt", truncation=True, max_length=512).input_ids.to("cuda")
    outputs = model.generate(input_ids=input_ids, max_length=256, do_sample=True)
    return TOKENIZER.decode(outputs[0], skip_special_tokens=True)

base_model = AutoModelForSeq2SeqLM.from_pretrained(BASE_MODEL).to("cuda")

sample_indices = random.sample(range(len(test_data)), 10)
for idx in sample_indices:
    input_text = TOKENIZER.decode(test_data[idx]["input_ids"], skip_special_tokens=True)
    ft_summary = generate_summary(model, input_text)
    base_summary = generate_summary(base_model, input_text)
    true_summary = TOKENIZER.decode(test_data[idx]["labels"], skip_special_tokens=True)

    print(f"\nInput:\n{input_text[:300]}...\n\n Fine-Tuned Summary:\n{ft_summary}\n\n Base Summary:\n{base_summary}\n\n Ground Truth:\n{true_summary}")

!pip install evaluate

import evaluate

rouge = evaluate.load("rouge")
bleu = evaluate.load("bleu")
bertscore = evaluate.load("bertscore")

refs = [TOKENIZER.decode(test_data[i]["labels"], skip_special_tokens=True) for i in sample_indices]
preds = [generate_summary(model, TOKENIZER.decode(test_data[i]["input_ids"], skip_special_tokens=True)) for i in sample_indices]

print("\n ROUGE:", rouge.compute(predictions=preds, references=refs))
print(" BLEU:", bleu.compute(predictions=preds, references=refs))
print(" BERTScore:", bertscore.compute(predictions=preds, references=refs, lang="en"))

import matplotlib.pyplot as plt

#Compute metrics
rouge_result = rouge.compute(predictions=preds, references=refs)
bleu_result = bleu.compute(predictions=preds, references=refs)
bertscore_result = bertscore.compute(predictions=preds, references=refs, lang="en")

#Extract relevant values
metrics = ['ROUGE-L', 'BLEU', 'BERTScore (F1)']
scores = [
    rouge_result['rougeL'],
    bleu_result['bleu'],
    sum(bertscore_result['f1']) / len(bertscore_result['f1'])  # average F1 score
]

#Bar chart
plt.figure(figsize=(8, 5))
bars = plt.bar(metrics, scores, color=['#66c2a5', '#fc8d62', '#8da0cb'])
plt.title("Evaluation Metrics for Fine-Tuned Model")
plt.ylabel("Score")
plt.ylim(0, 1)

for bar in bars:
    height = bar.get_height()
    plt.text(bar.get_x() + bar.get_width()/2.0, height, f'{height:.4f}', ha='center', va='bottom')

plt.grid(axis='y', linestyle='--', alpha=0.7)
plt.tight_layout()
plt.show()

!pip install together
import together

import time

#LLM-as-a-Judge via Together.ai

together.api_key = "YOUR-API-KEY"

prompt_template = """
Evaluate the summary on:
1. Fluency (1-5)
2. Factuality (1-5)
3. Coverage (1-5)

Provide a short justification for each rating.

Input: {input_text}
Summary: {generated_summary}
"""

#Select only one random sample for evaluation
sample_idx = sample_indices[0]  #Take just one sample from the indices

#Limit the input text and summary tokens to smaller sizes
input_text = TOKENIZER.decode(test_data[sample_idx]["input_ids"], skip_special_tokens=True)[:400]
summary = preds[0]  #Take the first summary

prompt = prompt_template.format(input_text=input_text, generated_summary=summary)

#Make the request to Together.ai for evaluation with further reduced tokens
response = together.Complete.create(
    model="meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8",
    prompt=prompt,
    max_tokens=500,
    temperature=0.7,
)

# Extract the response text
response_text = response['choices'][0]['text']

# Output the evaluation result for the single sample
print(f"\n Evaluation for Sample:\n{response_text}")

time.sleep(1)

!pip install langchain langchain-community

!pip install langchain langgraph openai

"""# KeywordAgent"""

from langchain.llms import OpenAI
from langchain.agents import Tool, AgentExecutor
from langchain.chains import LLMChain
from langchain.prompts import PromptTemplate
from langchain.chat_models import ChatOpenAI
import os

from langchain.llms import Together

# Set your Together API Key
import os
os.environ["TOGETHER_API_KEY"] = "YOUR-API-KEY"

# Initialize the Together LLM (example using Mistral)
llm = Together(
    model="mistralai/Mistral-7B-Instruct-v0.2",
    temperature=0.7,
    max_tokens=512
)

# Define Prompt
keyword_prompt = PromptTemplate.from_template(
    """
    You are a research assistant. Given the input keyword(s): "{user_input}", generate a list of related and expanded research keywords and phrases that can improve academic paper searches.

    Format the response as a numbered list of keywords.
    """
)

keyword_agent = LLMChain(llm=llm, prompt=keyword_prompt)

expanded_keywords = keyword_agent.run({"user_input": "artificial neural network"})
print(expanded_keywords)

"""# SearchAgent"""

!pip install feedparser

import feedparser
import urllib.parse

class SearchAgent:
    def __init__(self, max_results=10):
        self.base_url = "http://export.arxiv.org/api/query?"
        self.max_results = max_results

    def search_arxiv(self, keywords):
        # Create the query string with 'OR' joining the keywords
        query = "+OR+".join([f"all:{kw}" for kw in keywords])

        # URL encode the query string to prevent any issues with special characters
        encoded_query = urllib.parse.quote(query)

        # Construct the full URL with the encoded query
        url = f"{self.base_url}search_query={encoded_query}&start=0&max_results={self.max_results}"

        # Parse the feed
        feed = feedparser.parse(url)
        papers = []

        for entry in feed.entries:
            paper = {
                "title": entry.title,
                "abstract": entry.summary,
                "authors": [author.name for author in entry.authors],
                "published": entry.published,
                "link": entry.link
            }
            papers.append(paper)

        return papers

expanded_keywords = ["Deep learning", "Reinforcement Learning", "Hyperparameter optimization"]

# Initialize the SearchAgent
search_agent = SearchAgent(max_results=5)

# Search arXiv
papers = search_agent.search_arxiv(expanded_keywords)

# Print the results
for i, paper in enumerate(papers, 1):
    print(f"\nPaper {i}:")
    print(f"Title: {paper['title']}")
    print(f"Authors: {', '.join(paper['authors'])}")
    print(f"Published: {paper['published']}")
    print(f"Abstract: {paper['abstract'][:300]}...")  # Limiting abstract length for readability
    print(f"Link: {paper['link']}")

"""# RankAgent"""

!pip install requests

import requests
import time
from datetime import datetime

class RankAgent:
    def __init__(self, relevance_threshold=0.5):
        self.relevance_threshold = relevance_threshold

    def score_paper(self, paper, keywords):
        """
        Score each paper based on:
        - Citation count (if available)
        - Publication date (newer papers rank higher)
        - Relevance to the input keywords (inferred by an LLM API)
        """
        score = 0

        # Citation count: If available, higher citations get a higher score
        citation_count = paper.get("citations", 0)
        score += citation_count * 0.3

        published_date_str = paper["published"]
        published_date = datetime.strptime(published_date_str, "%Y-%m-%dT%H:%M:%SZ")

        current_date = datetime.utcnow()
        date_diff = (current_date - published_date).days
        score += max(0, 100 - date_diff) * 0.4

        abstract = paper["abstract"]
        relevance_score = self.get_relevance_score(abstract, keywords)
        score += relevance_score * 0.3

        return score

    def get_relevance_score(self, abstract, keywords):
        """
        Calculate the relevance score of the paper's abstract to the input keywords.
        Use Together.ai API for inference.
        """
        keyword_string = ", ".join(keywords)
        relevance_score = self.calculate_relevance_with_llm(abstract, keyword_string)
        return relevance_score

    def calculate_relevance_with_llm(self, abstract, keyword_string):
        """
        Use Together.ai to compute a relevance score between abstract and keywords.
        """

        api_key = "YOUR-API-KEY"
        endpoint = "https://api.together.xyz/inference"

        prompt = f"""Rate the relevance of the following abstract to these keywords on a scale from 0 to 1:

        Keywords: {keyword_string}

        Abstract: {abstract}

        Relevance score (only a number between 0 and 1):"""

        payload = {
        "model": "togethercomputer/llama-2-70b-chat",
        "prompt": prompt,
        "max_tokens": 5,
        "temperature": 0.0
        }

        headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
        }

        try:
          response = requests.post("https://api.together.xyz/inference", json=payload, headers=headers)
          response.raise_for_status()
          result_text = response.json()["choices"][0]["text"].strip()
          relevance_score = float(result_text)
          return min(max(relevance_score, 0), 1)
        except Exception as e:
          print(f"Error from Together.ai API: {e}")
          return 0.5



    def rank_papers(self, papers, keywords):
        scored_papers = []
        for paper in papers:
            score = self.score_paper(paper, keywords)
            paper["score"] = score
            scored_papers.append(paper)

        ranked_papers = sorted(scored_papers, key=lambda x: x["score"], reverse=True)
        return ranked_papers

#Rank the papers
rank_agent = RankAgent()
ranked_papers = rank_agent.rank_papers(papers, expanded_keywords)

print("\nAll Ranked Papers:")
for i, paper in enumerate(ranked_papers, 1):
        print(f"\nPaper {i}:")
        print(f"Title: {paper['title']}")
        print(f"Authors: {', '.join(paper['authors'])}")
        print(f"Published: {paper['published']}")
        print(f"Link: {paper['link']}")
        print(f"Score: {paper['score']:.4f}")

#Show top 2 papers
print("\nTop 2 Papers:")
for i, paper in enumerate(ranked_papers[:2], 1):
        print(f"\nTop Paper {i}:")
        print(f"Title: {paper['title']}")
        print(f"Authors: {', '.join(paper['authors'])}")
        print(f"Published: {paper['published']}")
        print(f"Link: {paper['link']}")
        print(f"Score: {paper['score']:.4f}")

"""# SummaryAgent"""

!pip install transformers
!pip install sentencepiece

# Use the same tokenizer and model already defined above
model.to("cuda")  # if not already
tokenizer = TOKENIZER  # already loaded

from transformers import pipeline
summarizer = pipeline("text2text-generation", model=model, tokenizer=tokenizer)

model.save_pretrained("finetuned-t5-lora")
tokenizer.save_pretrained("finetuned-t5-lora")

from peft import PeftModel
from transformers import AutoTokenizer, AutoModelForSeq2SeqLM

base_model = AutoModelForSeq2SeqLM.from_pretrained(BASE_MODEL)
model = PeftModel.from_pretrained(base_model, "finetuned-t5-lora")
tokenizer = AutoTokenizer.from_pretrained("finetuned-t5-lora")

class SummaryAgent:
    def __init__(self, summarizer_pipeline):
        self.summarizer = summarizer_pipeline

    def summarize_paper(self, paper):
        abstract = paper["abstract"]
        title = paper["title"]
        authors = ", ".join(paper["authors"])
        published = paper["published"]

        prompt = f"""
        You are a research assistant. Read the paper details below and generate a structured academic summary with the following sections:
        - Title
        - Authors
        - Date
        - Abstract Summary
        - Core Ideas
        - Possible Applications
        - Limitations or Challenges (if any)

        Paper Metadata:
        Title: {title}
        Authors: {authors}
        Published: {published}
        Abstract: {abstract}
        """

        try:
            result = self.summarizer(prompt, max_new_tokens=512, do_sample=True, temperature=0.7)[0]
            summary = result["generated_text"]
            return summary.strip()
        except Exception as e:
            print(f"Error during summarization: {e}")
            return "Summary could not be generated due to an error."

    def summarize_papers(self, papers):
        summaries = {}
        for i, paper in enumerate(papers, 1):
            print(f"\nSummarizing Paper {i}: {paper['title']}")
            summary = self.summarize_paper(paper)
            summaries[paper['title']] = summary
        return summaries

top_papers = ranked_papers[:2]
summary_agent = SummaryAgent(summarizer)
summaries = summary_agent.summarize_papers(top_papers)

for title, summary in summaries.items():
    print(f"\nSummary for: {title}\n{summary}\n{'-'*60}")
